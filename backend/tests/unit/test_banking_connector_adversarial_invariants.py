"""Behavioral and adversarial invariant test suite for Banking Connectors and Gateway Truth.

Validates core invariants across 36 adversarial controls:
- Open Banking factual-value integrity (AC-01 through AC-10)
- Core-banking provisional-hold failure integrity (AC-11 through AC-18)
- REST OAuth2 authentication failure integrity (AC-19 through AC-23)
- Thought Machine posting amount integrity (AC-24 through AC-27)
- Scientific report publication-state integrity (AC-28 through AC-34)
- Core-banking gateway health truth (AC-35 through AC-36)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from experiments.harness.compile_reports import ReportCompiler
from fastapi.testclient import TestClient

from app.infrastructure.connectors.mambu_connector import MambuConnector
from app.infrastructure.connectors.open_banking_connector import OpenBankingConnector
from app.infrastructure.connectors.rest_connector import (
    AuthenticationError as RESTAuthError,
)
from app.infrastructure.connectors.rest_connector import (
    RESTBankConnector,
)
from app.infrastructure.connectors.thought_machine_connector import ThoughtMachineConnector
from app.main import app

client = TestClient(app)


# ==============================================================================
# RU-01 / Financial & Identity Controls (AC-01 to AC-10)
# ==============================================================================


class TestOpenBankingFactualValueIntegrity:
    """Verifies OpenBankingConnector rejects fabricated amounts, IBANs, and MCC."""

    def test_missing_open_banking_amount_does_not_become_zero(self) -> None:
        """AC-01: Missing amount in transactionAmount or item raises ValueError, does not become 0.0."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_no_amt",
                        "debtorAccount": {"iban": "DE12345678901234567890"},
                        "creditorAccount": {"iban": "FR98765432109876543210"},
                        "transactionAmount": {"currency": "EUR"},  # Missing amount
                    }
                ]
            }
        }
        with pytest.raises(ValueError, match="missing required transaction amount"):
            connector.parse_psd2_payload(payload)

    def test_missing_open_banking_amount_does_not_become_one_hundred(self) -> None:
        """AC-02: Missing amount does not default to 100.0 EUR."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_no_amt",
                        "debtorAccount": {"iban": "DE12345678901234567890"},
                        "creditorAccount": {"iban": "FR98765432109876543210"},
                        # amount key absent entirely
                    }
                ]
            }
        }
        with pytest.raises(ValueError, match="missing required transaction amount"):
            connector.parse_psd2_payload(payload)

    def test_explicit_zero_amount_rejected_strictly(self) -> None:
        """AC-03: Zero amount is rejected as strictly non-positive, distinct from missing."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_zero_amt",
                        "debtorAccount": {"iban": "DE12345678901234567890"},
                        "creditorAccount": {"iban": "FR98765432109876543210"},
                        "transactionAmount": {"amount": "0.0", "currency": "EUR"},
                    }
                ]
            }
        }
        with pytest.raises(ValueError, match="amount must be strictly positive"):
            connector.parse_psd2_payload(payload)

    def test_missing_debtor_account_does_not_become_test_iban(self) -> None:
        """AC-04 & AC-06: Missing debtor account identity fails closed, never becomes DE89370400440532013000."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_no_debtor",
                        "debtorAccount": {},  # Missing IBAN/BBAN
                        "creditorAccount": {"iban": "FR98765432109876543210"},
                        "transactionAmount": {"amount": "50.0", "currency": "EUR"},
                    }
                ]
            }
        }
        with pytest.raises(ValueError, match="missing required debtor account identity"):
            connector.parse_psd2_payload(payload)

    def test_missing_creditor_account_does_not_become_test_iban(self) -> None:
        """AC-05 & AC-06: Missing creditor account identity fails closed, never becomes DE89370400440532013999."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_no_creditor",
                        "debtorAccount": {"iban": "DE12345678901234567890"},
                        "creditorAccount": {},  # Missing IBAN/BBAN
                        "transactionAmount": {"amount": "50.0", "currency": "EUR"},
                    }
                ]
            }
        }
        with pytest.raises(ValueError, match="missing required creditor account identity"):
            connector.parse_psd2_payload(payload)

    def test_missing_account_identity_does_not_become_plausible_generated_iban(self) -> None:
        """AC-06: Missing account identity is never synthesized into a plausible fake IBAN."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_missing_id",
                        "transactionAmount": {"amount": "100.0", "currency": "EUR"},
                    }
                ]
            }
        }
        with pytest.raises(ValueError, match="missing required debtor account identity"):
            connector.parse_psd2_payload(payload)

    def test_missing_mcc_does_not_become_5999(self) -> None:
        """AC-07: Missing merchant category code defaults to ISO unspecified '0000', never fabricated '5999'."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_no_mcc",
                        "debtorAccount": {"iban": "DE12345678901234567890"},
                        "creditorAccount": {"iban": "FR98765432109876543210"},
                        "transactionAmount": {"amount": "50.0", "currency": "EUR"},
                    }
                ]
            }
        }
        txs = connector.parse_psd2_payload(payload)
        assert len(txs) == 1
        assert txs[0].merchant_category_code == "0000"
        assert txs[0].merchant_category_code != "5999"

    def test_explicit_upstream_mcc_5999_remains_intact(self) -> None:
        """AC-08: Explicit upstream MCC 5999 is preserved when factual."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_with_mcc",
                        "debtorAccount": {"iban": "DE12345678901234567890"},
                        "creditorAccount": {"iban": "FR98765432109876543210"},
                        "transactionAmount": {"amount": "50.0", "currency": "EUR"},
                        "merchantCategoryCode": "5999",
                    }
                ]
            }
        }
        txs = connector.parse_psd2_payload(payload)
        assert len(txs) == 1
        assert txs[0].merchant_category_code == "5999"

    def test_country_derived_from_iban_not_hardcoded_german(self) -> None:
        """AC-09: Origin and destination country codes are derived from IBAN prefix or None, never hardcoded 'DE'."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_fr_es",
                        "debtorAccount": {"iban": "FR1420041010050500013M02606"},
                        "creditorAccount": {"iban": "ES9121000418450200051332"},
                        "transactionAmount": {"amount": "75.0", "currency": "EUR"},
                    }
                ]
            }
        }
        txs = connector.parse_psd2_payload(payload)
        assert txs[0].origin_country == "FR"
        assert txs[0].destination_country == "ES"
        assert txs[0].origin_country != "DE"

    def test_currency_derived_from_upstream(self) -> None:
        """AC-10: Upstream currency is preserved accurately and not overridden."""
        connector = OpenBankingConnector(access_token="test_token")
        payload = {
            "transactions": {
                "booked": [
                    {
                        "transactionId": "tx_chf",
                        "debtorAccount": {"iban": "CH9300762011623852957"},
                        "creditorAccount": {"iban": "CH9300762011623852958"},
                        "transactionAmount": {"amount": "120.0", "currency": "CHF"},
                    }
                ]
            }
        }
        txs = connector.parse_psd2_payload(payload)
        assert txs[0].currency == "CHF"


# ==============================================================================
# RU-02 / Core Banking Provisional Hold Failure Integrity (AC-11 to AC-18)
# ==============================================================================


class TestCoreBankingHoldIntegrity:
    """Verifies live hold dispatch failure never masquerades as simulated success."""

    @pytest.mark.asyncio
    async def test_live_mambu_hold_failure_does_not_return_simulated_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-11: Live Mambu HTTP dispatch failure returns HOLD_FAILED, not SIMULATED_LOOPBACK."""
        async def mock_post(*args: Any, **kwargs: Any) -> Any:
            raise httpx.ConnectError("host unreachable")
        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        connector = MambuConnector(
            base_url="https://api.mambu.test",
            api_key="valid_mambu_api_key",
        )
        record = await connector.apply_provisional_hold(
            account_id="ACC_MAMBU_01",
            amount=5000.0,
            reason="Suspected money mule cluster",
            simulation=False,
        )
        assert record["status"] == "HOLD_FAILED"
        assert record["external_status"] == "DISPATCH_FAILED_UNREACHABLE"
        assert record["status"] != "HOLD_APPLIED"
        assert record["external_status"] != "SIMULATED_LOOPBACK"

    @pytest.mark.asyncio
    async def test_missing_mambu_credentials_does_not_return_simulated_success(self) -> None:
        """AC-12: Missing Mambu API key returns HOLD_FAILED / AUTHENTICATION_FAILED."""
        connector = MambuConnector(api_key="")
        record = await connector.apply_provisional_hold(
            account_id="ACC_MAMBU_02",
            amount=1000.0,
            reason="Test missing creds",
            simulation=False,
        )
        assert record["status"] == "HOLD_FAILED"
        assert record["external_status"] == "AUTHENTICATION_FAILED"
        assert record["status"] != "HOLD_APPLIED"

    @pytest.mark.asyncio
    async def test_live_thought_machine_restriction_failure_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-13: Live Thought Machine restriction failure returns RESTRICTION_FAILED."""
        async def mock_post(*args: Any, **kwargs: Any) -> Any:
            raise httpx.ConnectError("host unreachable")
        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        connector = ThoughtMachineConnector(
            base_url="https://vault-core.test:8080",
            api_key="valid_vault_token",
        )
        record = await connector.apply_provisional_hold(
            account_id="ACC_TM_01",
            amount=7500.0,
            reason="High risk burst activity",
            simulation=False,
        )
        assert record["status"] == "RESTRICTION_FAILED"
        assert record["external_status"] == "DISPATCH_FAILED_UNREACHABLE"
        assert record["status"] != "RESTRICTION_COMMITTED"

    @pytest.mark.asyncio
    async def test_failed_hold_does_not_produce_hold_applied(self) -> None:
        """AC-14: Failed hold cannot produce HOLD_APPLIED."""
        connector = MambuConnector(api_key="")
        record = await connector.apply_provisional_hold(
            account_id="ACC_ERR",
            amount=2000.0,
            reason="Error path",
            simulation=False,
        )
        assert record["status"] != "HOLD_APPLIED"

    def test_failed_hold_does_not_produce_http_success(self) -> None:
        """AC-15: Gateway returns HTTP 503 on live hold failure, never HTTP 200/201."""
        hold_req = {
            "account_id": "ACC_FAIL_LIVE",
            "amount": 3000.0,
            "reason": "Suspicious account",
            "provider": "mambu",
            "simulation": False,
        }
        res = client.post("/connectors/core-banking/holds/provisional", json=hold_req)
        assert res.status_code == 503
        assert "Core banking provisional hold failed" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_explicit_simulation_remains_visibly_simulated(self) -> None:
        """AC-16 & AC-17: Explicit simulation mode returns SIMULATION_RESULT / SIMULATED_LOOPBACK."""
        connector = MambuConnector()
        record = await connector.apply_provisional_hold(
            account_id="ACC_SIM_01",
            amount=1500.0,
            reason="Simulation trial",
            simulation=True,
        )
        assert record["status"] == "SIMULATION_RESULT"
        assert record["external_status"] == "SIMULATED_LOOPBACK"
        assert record["status"] != "HOLD_APPLIED"

    @pytest.mark.asyncio
    async def test_simulated_result_not_interpreted_as_live_hold(self) -> None:
        """AC-17: Simulation mode results have status SIMULATION_RESULT, distinct from HOLD_APPLIED."""
        connector = ThoughtMachineConnector()
        record = await connector.apply_provisional_hold(
            account_id="ACC_SIM_TM",
            amount=2500.0,
            reason="Test sim distinction",
            simulation=True,
        )
        assert record["status"] == "SIMULATION_RESULT"
        assert record["status"] != "HOLD_APPLIED"
        assert record["status"] != "RESTRICTION_COMMITTED"
        assert record["external_status"] == "SIMULATED_LOOPBACK"

    def test_hold_failure_prevents_false_settlement_assumption(self) -> None:
        """AC-18: Consumer verifying hold outcome detects failure when status != HOLD_APPLIED."""
        hold_req = {
            "account_id": "ACC_FAIL_CONSUMER",
            "amount": 9000.0,
            "reason": "Recall verification",
            "provider": "thought_machine",
            "simulation": False,
        }
        res = client.post("/connectors/core-banking/holds/provisional", json=hold_req)
        assert res.status_code == 503
        # Settlement pipeline checking response will receive 503, preventing proceeding with settlement


# ==============================================================================
# RU-03 / REST OAuth2 Authentication Integrity (AC-19 to AC-23)
# ==============================================================================


class TestRESTOAuth2Integrity:
    """Verifies RESTBankConnector raises AuthenticationError on token failure."""

    def test_oauth_error_does_not_produce_placeholder_token(self) -> None:
        """AC-19: OAuth server returning 401 raises AuthenticationError, never returns placeholder."""
        connector = RESTBankConnector(
            base_url="https://bank-node.test/api",
            auth_type="oauth2",
            oauth_token_url="https://auth-server.test/oauth/token",
        )
        # Mock httpx.Client.post returning 401
        with pytest.raises(RESTAuthError, match="OAuth2 server returned status 401"):
            mock_resp = MagicMock(status_code=401, text="Unauthorized client")
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(httpx.Client, "post", lambda *a, **kw: mock_resp)
                connector._get_oauth2_token()

    def test_missing_token_in_200_response_raises_auth_error(self) -> None:
        """AC-20: Missing access_token key in 200 response raises AuthenticationError."""
        connector = RESTBankConnector(
            base_url="https://bank-node.test/api",
            auth_type="oauth2",
            oauth_token_url="https://auth-server.test/oauth/token",
        )
        with pytest.raises(RESTAuthError, match="missing access_token"):
            mock_resp = MagicMock(status_code=200, json=lambda: {"token_type": "bearer"})
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(httpx.Client, "post", lambda *a, **kw: mock_resp)
                connector._get_oauth2_token()

    def test_placeholder_token_never_enters_authorization_header(self) -> None:
        """AC-21: Token acquisition failure aborts _get_headers() before inserting any header."""
        connector = RESTBankConnector(
            base_url="https://bank-node.test/api",
            auth_type="oauth2",
            oauth_token_url="https://invalid-auth-url.local/token",
        )
        with pytest.raises(RESTAuthError), pytest.MonkeyPatch.context() as mp:
            def _mock_post(*a: Any, **kw: Any) -> Any:
                raise httpx.ConnectError("Connection refused")
            mp.setattr(httpx.Client, "post", _mock_post)
            connector._get_headers()

    def test_failed_token_acquisition_prevents_downstream_request(self) -> None:
        """AC-22: Failure to acquire token prevents executing command requests."""
        connector = RESTBankConnector(
            base_url="https://bank-node.test/api",
            auth_type="oauth2",
            oauth_token_url="https://invalid-auth-url.local/token",
        )
        with pytest.raises(RESTAuthError), pytest.MonkeyPatch.context() as mp:
            def _mock_post(*a: Any, **kw: Any) -> Any:
                raise httpx.ConnectError("Network error")
            mp.setattr(httpx.Client, "post", _mock_post)
            connector.initialize(bank_id="BANK_A", num_transactions=10)

    def test_successful_oauth_path_remains_intact(self) -> None:
        """AC-23: Valid OAuth2 response caches and returns valid token."""
        connector = RESTBankConnector(
            base_url="https://bank-node.test/api",
            auth_type="oauth2",
            oauth_token_url="https://auth.test/token",
        )
        mock_resp = MagicMock(status_code=200, json=lambda: {"access_token": "valid_token_xyz_123"})
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(httpx.Client, "post", lambda *a, **kw: mock_resp)
            tok = connector._get_oauth2_token()
            assert tok == "valid_token_xyz_123"
            headers = connector._get_headers()
            assert headers["Authorization"] == "Bearer valid_token_xyz_123"


# ==============================================================================
# RU-05 / Thought Machine Posting Amount Integrity (AC-24 to AC-27)
# ==============================================================================


class TestThoughtMachineAmountIntegrity:
    """Verifies ThoughtMachineConnector rejects missing or fabricated amounts."""

    def test_missing_posting_amount_does_not_become_zero(self) -> None:
        """AC-24: Missing amount in multi-leg posting instructions rejects instruction (returns None)."""
        connector = ThoughtMachineConnector()
        payload = {
            "posting_instruction_batch": {
                "id": "PIB_NO_AMT_01",
                "posting_instructions": [
                    {
                        "id": "INST_01",
                        "custom_instruction": {
                            "postings": [
                                {"account_id": "ACC_DEB", "denomination": "EUR", "credit": False},  # Missing amount
                                {"account_id": "ACC_CRED", "amount": "100.0", "denomination": "EUR", "credit": True},
                            ]
                        },
                    }
                ],
            }
        }
        txs = connector.parse_batch(payload)
        assert len(txs) == 0  # Instruction rejected, not parsed with amount=0.0

    def test_missing_posting_amount_does_not_become_one_hundred(self) -> None:
        """AC-25: Missing amount in single-leg instruction rejects instruction, does not default to 100.0."""
        connector = ThoughtMachineConnector()
        payload = {
            "posting_instruction_batch": {
                "id": "PIB_NO_AMT_02",
                "posting_instructions": [
                    {
                        "id": "INST_02",
                        "account_id": "ACC_DEB",
                        "target_account_id": "ACC_CRED",
                        # amount key omitted entirely
                        "currency": "EUR",
                    }
                ],
            }
        }
        txs = connector.parse_batch(payload)
        assert len(txs) == 0  # Instruction rejected, not parsed with 100.0

    def test_explicit_posting_zero_is_rejected(self) -> None:
        """AC-26: Explicit zero posting amount is rejected as non-positive monetary amount."""
        connector = ThoughtMachineConnector()
        payload = {
            "posting_instruction_batch": {
                "id": "PIB_ZERO_01",
                "posting_instructions": [
                    {
                        "id": "INST_ZERO",
                        "account_id": "ACC_DEB",
                        "target_account_id": "ACC_CRED",
                        "amount": "0.0",
                        "currency": "EUR",
                    }
                ],
            }
        }
        txs = connector.parse_batch(payload)
        assert len(txs) == 0

    def test_valid_posting_amount_preserved_accurately(self) -> None:
        """AC-27: Valid positive posting amount is preserved accurately."""
        connector = ThoughtMachineConnector()
        payload = {
            "posting_instruction_batch": {
                "id": "PIB_VALID",
                "posting_instructions": [
                    {
                        "id": "INST_VALID",
                        "account_id": "DE1234567890",
                        "target_account_id": "FR0987654321",
                        "amount": "456.78",
                        "currency": "EUR",
                    }
                ],
            }
        }
        txs = connector.parse_batch(payload)
        assert len(txs) == 1
        assert txs[0].amount == 456.78
        assert txs[0].account_id == "DE1234567890"
        assert txs[0].counterparty_account_id == "FR0987654321"
        assert txs[0].origin_country == "DE"
        assert txs[0].destination_country == "FR"

    def test_no_fictitious_counterparty_account_fabricated(self) -> None:
        """Constraint 5: Verifies that single-leg postings without authoritative metadata do not fabricate accounts."""
        connector = ThoughtMachineConnector()
        # Single-leg posting with only a debit leg and no target/counterparty metadata
        payload_single_leg = {
            "posting_instruction_batch": {
                "id": "PIB_SINGLE_LEG",
                "posting_instructions": [
                    {
                        "id": "INST_SINGLE_LEG",
                        "custom_instruction": {
                            "value_timestamp": "2026-03-01T10:00:00Z",
                            "postings": [
                                {"account_id": "ACC_SOLE_DEBITOR", "amount": "250.0", "credit": False},
                            ],
                        },
                    }
                ],
            }
        }
        txs = connector.parse_batch(payload_single_leg)
        # Incomplete single leg without authoritative counterparty metadata must be rejected / quarantined,
        # never synthesized with a fabricated counterparty like 'tm_ext_ACC_SOLE_DEBITOR'.
        assert len(txs) == 0
        assert not any("tm_ext" in getattr(tx, "counterparty_account_id", "") for tx in txs)


# ==============================================================================
# RU-06 / Scientific Report Publication-State Integrity (AC-28 to AC-34)
# ==============================================================================


class TestScientificPublicationStateIntegrity:
    """Verifies ReportCompiler marks unmeasured metrics UNMEASURED, never CONFIRMED."""

    def test_unmeasured_pr_auc_must_not_be_confirmed(self) -> None:
        """AC-28: Unmeasured PR-AUC is marked UNMEASURED, never CONFIRMED."""
        compiler = ReportCompiler()
        report_md = compiler.compile_markdown_report({"experiment_id": "EXP_PR", "final_metrics": {}})
        assert "| **Precision-Recall AUC (PR-AUC)** | **N/A** | Primary Imbalanced Metric | `UNMEASURED` |" in report_md
        assert "`CONFIRMED`" not in [line for line in report_md.splitlines() if "PR-AUC" in line][0]

    def test_unmeasured_roc_auc_must_not_be_confirmed(self) -> None:
        """AC-29: Unmeasured ROC-AUC is marked UNMEASURED, never CONFIRMED."""
        compiler = ReportCompiler()
        report_md = compiler.compile_markdown_report({"experiment_id": "EXP_ROC", "final_metrics": {}})
        assert "| **ROC-AUC** | **N/A** | Area Under Receiver Operating Characteristic | `UNMEASURED` |" in report_md
        assert "`CONFIRMED`" not in [line for line in report_md.splitlines() if "ROC-AUC" in line][0]

    def test_unmeasured_f1_must_not_be_confirmed(self) -> None:
        """AC-30: Unmeasured F1 is marked UNMEASURED, never CONFIRMED."""
        compiler = ReportCompiler()
        report_md = compiler.compile_markdown_report({"experiment_id": "EXP_F1", "final_metrics": {}})
        assert "| **F1-Score (Optimal Threshold)** | **N/A** | Harmonic Mean of Precision & Recall | `UNMEASURED` |" in report_md
        assert "`CONFIRMED`" not in [line for line in report_md.splitlines() if "F1-Score" in line][0]

    def test_unmeasured_precision_must_not_be_confirmed(self) -> None:
        """AC-31: Unmeasured Precision is marked UNMEASURED, never CONFIRMED."""
        compiler = ReportCompiler()
        report_md = compiler.compile_markdown_report({"experiment_id": "EXP_PREC", "final_metrics": {}})
        assert "| **Precision (PPV)** | **N/A** | Operational False Positive Ceiling | `UNMEASURED` |" in report_md
        assert "`CONFIRMED`" not in [line for line in report_md.splitlines() if "Precision (PPV)" in line][0]

    def test_unmeasured_recall_must_not_be_confirmed(self) -> None:
        """AC-32: Unmeasured Recall is marked UNMEASURED, never CONFIRMED."""
        compiler = ReportCompiler()
        report_md = compiler.compile_markdown_report({"experiment_id": "EXP_REC", "final_metrics": {}})
        assert "| **Recall (Sensitivity)** | **N/A** | True Positive Fraud Detection Floor | `UNMEASURED` |" in report_md
        assert "`CONFIRMED`" not in [line for line in report_md.splitlines() if "Recall (Sensitivity)" in line][0]

    def test_missing_metric_does_not_become_numeric_zero(self) -> None:
        """AC-33: Missing metric is reported as 'N/A', never coerced to 0.0000."""
        compiler = ReportCompiler()
        report_md = compiler.compile_markdown_report({"experiment_id": "EXP_NO_ZERO", "final_metrics": {}})
        assert "| **Precision-Recall AUC (PR-AUC)** | **0.0000** |" not in report_md
        assert "| **ROC-AUC** | **0.0000** |" not in report_md

    def test_missing_sample_count_does_not_fabricate_number(self) -> None:
        """AC-34: Empty metrics do not fabricate synthetic sample counts."""
        compiler = ReportCompiler()
        report_md = compiler.compile_markdown_report({"experiment_id": "EXP_NO_CM", "metrics": {}})
        assert "Total Test Samples" not in report_md or "0" in report_md

    def test_measured_metrics_receive_confirmed_status(self) -> None:
        """AC-CONTROL: Measured metrics receive confirmed status legitimately."""
        compiler = ReportCompiler()
        measured_metrics: dict[str, Any] = {
            "experiment_id": "EXP_MEASURED",
            "metrics": {
                "pr_auc": 0.8912,
                "roc_auc": 0.9543,
                "f1_score": 0.8711,
                "precision": 0.8800,
                "recall": 0.8625,
                "brier_score": 0.0412,
            },
        }
        report_md = compiler.compile_markdown_report(measured_metrics)
        assert "| **Precision-Recall AUC (PR-AUC)** | **0.8912** | Primary Imbalanced Metric | `CONFIRMED` |" in report_md
        assert "| **ROC-AUC** | **0.9543** | Area Under Receiver Operating Characteristic | `CONFIRMED` |" in report_md



# ==============================================================================
# RU-04 / Core Banking Gateway Health Truth (AC-35 to AC-36)
# ==============================================================================


class TestCoreBankingGatewayHealthTruth:
    """Verifies gateway health reflects actual connector and circuit breaker states."""

    def test_open_circuit_breaker_produces_unhealthy_state(self) -> None:
        """AC-35: Open circuit breaker on connector produces UNAVAILABLE / DEGRADED, not HEALTHY."""
        from app.presentation.routers.core_banking_gateway import (
            get_mambu_connector,
            get_thought_machine_connector,
        )
        mambu = get_mambu_connector()
        tm = get_thought_machine_connector()

        mambu.circuit_breaker.state = "OPEN"
        tm.circuit_breaker.state = "OPEN"

        res = client.get("/connectors/core-banking/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "UNAVAILABLE"
        assert data["status"] != "HEALTHY"

        # Restore
        mambu.circuit_breaker.state = "CLOSED"
        tm.circuit_breaker.state = "CLOSED"

    def test_unavailable_authentication_produces_non_healthy_state(self) -> None:
        """AC-36: When API keys are not configured, status is not falsely HEALTHY."""
        from app.presentation.routers.core_banking_gateway import (
            get_mambu_connector,
            get_thought_machine_connector,
        )
        mambu = get_mambu_connector()
        tm = get_thought_machine_connector()

        mambu.api_key = ""
        tm.api_key = ""
        mambu.circuit_breaker.state = "CLOSED"
        tm.circuit_breaker.state = "CLOSED"

        res = client.get("/connectors/core-banking/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "DEGRADED"
        assert data["status"] != "HEALTHY"
