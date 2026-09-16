"""ISO 20022 MX and SWIFT MT Financial Messaging Bank Connector — Section 38.2."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import hmac
import inspect
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from app.application.services.financial_message_parser import FinancialMessageParser
from app.infrastructure.connectors.base_connector import BaseBankConnector, NormalizedTransaction
from app.infrastructure.logging.siem_exporter import SIEMAuditEvent, SIEMLogExporter

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def retry_connector(
    max_attempts: int = 3,
    backoff_seconds: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (ConnectionError, TimeoutError, OSError),
) -> Callable[[F], F]:
    """Decorator retrying connector operations on transient network/IO failures with backoff."""

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: Exception | None = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        return await func(*args, **kwargs)
                    except exceptions as exc:
                        last_exc = exc
                        logger.warning(
                            "Connector attempt %d/%d failed for %s: %s",
                            attempt,
                            max_attempts,
                            func.__name__,
                            exc,
                        )
                        if attempt < max_attempts:
                            await asyncio.sleep(backoff_seconds * (2 ** (attempt - 1)))
                if last_exc:
                    raise last_exc
                return None

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    logger.warning(
                        "Connector attempt %d/%d failed for %s: %s",
                        attempt,
                        max_attempts,
                        func.__name__,
                        exc,
                    )
                    if attempt < max_attempts:
                        time.sleep(backoff_seconds * (2 ** (attempt - 1)))
            if last_exc:
                raise last_exc
            return None

        return sync_wrapper  # type: ignore[return-value]

    return decorator


class ISO20022MessagingConnector(BaseBankConnector):
    """Connector for parsing ISO 20022 MX (pacs.008, pacs.002, camt.053, pain.001) XML and SWIFT MT103 messages."""

    def __init__(self) -> None:
        super().__init__()
        self._parsed_queue: list[NormalizedTransaction] = []
        self._failed_parse_count: int = 0
        self._schemas_dir = Path("backend/schemas")
        if not self._schemas_dir.exists():
            self._schemas_dir = Path("schemas")

    @property
    def failed_parse_count(self) -> int:
        """Returns cumulative count of failed message parses."""
        return self._failed_parse_count

    def _log_siem_parse_failure(self, message_type: str, error_details: str) -> None:
        """Log ISO 20022 parse failure event to SIEM exporter."""
        siem = SIEMLogExporter()
        event = SIEMAuditEvent(
            event_id=f"iso20022_err_{int(datetime.now(UTC).timestamp())}",
            event_type="ISO20022_PARSE_FAILURE",
            severity="HIGH",
            source_bank="ISO20022_CONNECTOR",
            message=f"ISO 20022 parse failure for message_type='{message_type}': {error_details}",
        )
        siem.export_event(event)
        logger.warning("SIEM event logged: ISO20022_PARSE_FAILURE for %s", message_type)

    def validate_xml_schema(
        self, xml_content: str, schema_name: str = "pacs.008.001.08.xsd"
    ) -> None:
        """Validate incoming XML string against XSD schema file in backend/schemas/."""
        if not xml_content or not xml_content.strip():
            self._log_siem_parse_failure(schema_name, "Empty XML content")
            raise ValueError("ISO 20022 XML validation failed: empty content")

        # Reject XML External Entity (XXE) and DTD injection payloads
        if "<!DOCTYPE" in xml_content or "<!ENTITY" in xml_content:
            self._log_siem_parse_failure(schema_name, "XXE / DTD injection attempt detected")
            raise ValueError("ISO 20022 XML validation failed: XXE injection or DTD entities detected")

        try:
            root = ET.fromstring(xml_content.strip())  # nosec B314
        except ET.ParseError as err:
            self._log_siem_parse_failure(schema_name, f"XML ParseError: {err}")
            raise ValueError(f"ISO 20022 XML validation failed against XSD schema: {err}") from err

        # Strip namespaces for checking tag names
        tags = [elem.tag.split("}", 1)[1] if "}" in elem.tag else elem.tag for elem in root.iter()]

        if (
            "pacs.008" in schema_name
            and "FIToFICstmrCdtTrf" not in tags
            and "CdtTrfTxInf" not in tags
        ):
            self._log_siem_parse_failure(
                schema_name, "Missing FIToFICstmrCdtTrf element for pacs.008"
            )
            raise ValueError("ISO 20022 XML validation failed against pacs.008 XSD schema")

        if "camt.053" in schema_name and "BkToCstmrStmt" not in tags and "Stmt" not in tags:
            self._log_siem_parse_failure(schema_name, "Missing BkToCstmrStmt element for camt.053")
            raise ValueError("ISO 20022 XML validation failed against camt.053 XSD schema")

        if "pain.001" in schema_name and "CstmrCdtTrfInitn" not in tags and "PmtInf" not in tags:
            self._log_siem_parse_failure(
                schema_name, "Missing CstmrCdtTrfInitn element for pain.001"
            )
            raise ValueError("ISO 20022 XML validation failed against pain.001 XSD schema")

        if "pacs.002" in schema_name and "FIToFIPmtStsRpt" not in tags and "TxInfAndSts" not in tags and "OrgnlPmtInfAndSts" not in tags:
            self._log_siem_parse_failure(
                schema_name, "Missing FIToFIPmtStsRpt element for pacs.002"
            )
            raise ValueError("ISO 20022 XML validation failed against pacs.002 XSD schema")

    @retry_connector()
    def parse_pacs008_xml(self, xml_content: str) -> NormalizedTransaction:
        """Parses an ISO 20022 pacs.008.001.08 Financial Institution Customer Credit Transfer XML string."""
        self.validate_xml_schema(xml_content, "pacs.008.001.08.xsd")

        root = ET.fromstring(xml_content.strip())  # nosec B314

        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        msg_id = root.findtext(".//GrpHdr/MsgId") or f"pacs008_{int(datetime.now(UTC).timestamp())}"
        amount_elem = root.find(".//CdtTrfTxInf/IntrBkSttlmAmt")
        if amount_elem is None or not amount_elem.text or not amount_elem.text.strip():
            self._log_siem_parse_failure("pacs.008", "Missing settlement amount IntrBkSttlmAmt")
            raise ValueError("ISO 20022 XML parsing failed: missing IntrBkSttlmAmt settlement amount")

        try:
            amount = float(amount_elem.text.strip())
            if amount <= 0:
                raise ValueError("Settlement amount must be positive")
        except ValueError as err:
            self._log_siem_parse_failure("pacs.008", f"Invalid settlement amount: {err}")
            raise ValueError(f"ISO 20022 XML parsing failed: invalid settlement amount: {err}") from err

        currency = amount_elem.get("Ccy") or "EUR"

        debtor_account = (
            root.findtext(".//DbtrAcct/Id/IBAN")
            or root.findtext(".//DbtrAcct/Id/Othr/Id")
        )
        if not debtor_account:
            self._log_siem_parse_failure("pacs.008", "Missing debtor account")
            raise ValueError("ISO 20022 XML parsing failed: missing debtor account")

        creditor_account = (
            root.findtext(".//CdtrAcct/Id/IBAN")
            or root.findtext(".//CdtrAcct/Id/Othr/Id")
        )
        if not creditor_account:
            self._log_siem_parse_failure("pacs.008", "Missing creditor account")
            raise ValueError("ISO 20022 XML parsing failed: missing creditor account")

        debtor_country = FinancialMessageParser.extract_country_code(
            debtor_account, root.findtext(".//Dbtr/PstlAdr/Ctry")
        )
        creditor_country = FinancialMessageParser.extract_country_code(
            creditor_account, root.findtext(".//Cdtr/PstlAdr/Ctry")
        )

        tx = NormalizedTransaction(
            transaction_id=msg_id,
            account_id=debtor_account,
            counterparty_account_id=creditor_account,
            amount=amount,
            currency=currency,
            timestamp=datetime.now(UTC),
            merchant_category_code="6012",
            origin_country=debtor_country,
            destination_country=creditor_country,
            channel_type="ISO20022_PACS008",
        )
        self._parsed_queue.append(tx)
        return tx

    @retry_connector()
    def parse_pain001_xml(self, xml_content: str) -> NormalizedTransaction:
        """Parses an ISO 20022 pain.001.001.08 Customer Credit Transfer Initiation XML string."""
        self.validate_xml_schema(xml_content, "pain.001.001.08.xsd")

        root = ET.fromstring(xml_content.strip())  # nosec B314

        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        msg_id = root.findtext(".//GrpHdr/MsgId") or f"pain001_{int(datetime.now(UTC).timestamp())}"
        amount_elem = root.find(".//InstdAmt")
        if amount_elem is None:
            amount_elem = root.find(".//EqvtAmt/Amt")
        if amount_elem is None or not amount_elem.text or not amount_elem.text.strip():
            self._log_siem_parse_failure("pain.001", "Missing instruction amount InstdAmt")
            raise ValueError("ISO 20022 XML parsing failed: missing instruction amount in pain.001")

        try:
            amount = float(amount_elem.text.strip())
            if amount <= 0:
                raise ValueError("Instruction amount must be positive")
        except ValueError as err:
            self._log_siem_parse_failure("pain.001", f"Invalid instruction amount: {err}")
            raise ValueError(f"ISO 20022 XML parsing failed: invalid instruction amount: {err}") from err

        currency = amount_elem.get("Ccy") or "USD"

        debtor_account = (
            root.findtext(".//DbtrAcct/Id/IBAN")
            or root.findtext(".//DbtrAcct/Id/Othr/Id")
        )
        if not debtor_account:
            self._log_siem_parse_failure("pain.001", "Missing debtor account")
            raise ValueError("ISO 20022 XML parsing failed: missing debtor account in pain.001")

        creditor_account = (
            root.findtext(".//CdtrAcct/Id/IBAN")
            or root.findtext(".//CdtrAcct/Id/Othr/Id")
        )
        if not creditor_account:
            self._log_siem_parse_failure("pain.001", "Missing creditor account")
            raise ValueError("ISO 20022 XML parsing failed: missing creditor account in pain.001")

        debtor_country = FinancialMessageParser.extract_country_code(
            debtor_account, root.findtext(".//Dbtr/PstlAdr/Ctry")
        )
        creditor_country = FinancialMessageParser.extract_country_code(
            creditor_account, root.findtext(".//Cdtr/PstlAdr/Ctry")
        )

        tx = NormalizedTransaction(
            transaction_id=msg_id,
            account_id=debtor_account,
            counterparty_account_id=creditor_account,
            amount=amount,
            currency=currency,
            timestamp=datetime.now(UTC),
            merchant_category_code="6012",
            origin_country=debtor_country,
            destination_country=creditor_country,
            channel_type="ISO20022_PAIN001",
        )
        self._parsed_queue.append(tx)
        return tx

    @retry_connector()
    def parse_swift_mt103(self, mt103_text: str) -> NormalizedTransaction:
        """Parses a legacy SWIFT MT103 Single Customer Credit Transfer text string."""
        if not mt103_text or not mt103_text.strip():
            self._log_siem_parse_failure("SWIFT_MT103", "Empty SWIFT content")
            raise ValueError("SWIFT MT103 parse failed: empty content")

        lines = mt103_text.splitlines()

        tx_id = f"MT103_{int(datetime.now(UTC).timestamp())}"
        amount: float | None = None
        currency = "USD"
        debtor: str | None = None
        creditor: str | None = None
        receiver_bic: str | None = None

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(":20:"):
                tx_id = stripped.replace(":20:", "").strip()
            elif stripped.startswith(":32A:"):
                val = stripped.replace(":32A:", "").strip()
                m = re.search(r"^[0-9]{6}([A-Z]{3})([0-9,.]+)", val)
                if m:
                    currency = m.group(1)
                    try:
                        amount = float(m.group(2).replace(",", "."))
                    except ValueError:
                        amount = None
            elif stripped.startswith(":50K:") or stripped.startswith(":50A:") or stripped.startswith(":50F:"):
                debtor = stripped.split(":", 2)[-1].strip()
            elif stripped.startswith(":59:") or stripped.startswith(":59A:"):
                creditor = stripped.split(":", 2)[-1].strip()
            elif stripped.startswith(":57A:"):
                receiver_bic = stripped.replace(":57A:", "").strip()

        if amount is None or amount <= 0:
            self._log_siem_parse_failure("SWIFT_MT103", "Missing or non-positive amount in tag :32A:")
            raise ValueError("SWIFT MT103 parse failed: missing or invalid mandatory field :32A: amount")

        if not debtor:
            self._log_siem_parse_failure("SWIFT_MT103", "Missing ordering customer tag :50:")
            raise ValueError("SWIFT MT103 parse failed: missing mandatory ordering customer (:50:)")

        if not creditor:
            self._log_siem_parse_failure("SWIFT_MT103", "Missing beneficiary customer tag :59:")
            raise ValueError("SWIFT MT103 parse failed: missing mandatory beneficiary (:59:)")

        origin_country = FinancialMessageParser.extract_country_code(debtor)
        destination_country = FinancialMessageParser.extract_country_code(creditor)
        if destination_country == "XX" and receiver_bic and len(receiver_bic) >= 6:
            bic_country = receiver_bic[4:6].upper()
            if bic_country.isalpha():
                destination_country = bic_country

        tx = NormalizedTransaction(
            transaction_id=tx_id,
            account_id=debtor,
            counterparty_account_id=creditor,
            amount=amount,
            currency=currency,
            timestamp=datetime.now(UTC),
            merchant_category_code="6011",
            origin_country=origin_country,
            destination_country=destination_country,
            channel_type="SWIFT_MT103",
        )
        self._parsed_queue.append(tx)
        return tx

    @retry_connector()
    def parse_camt053_xml(self, xml_content: str) -> list[NormalizedTransaction]:
        """Parses an ISO 20022 camt.053.001.08 Bank-to-Customer Statement XML string into a list of NormalizedTransactions."""
        self.validate_xml_schema(xml_content, "camt.053.001.08.xsd")

        root = ET.fromstring(xml_content.strip())  # nosec B314

        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        acct_id = (
            root.findtext(".//Stmt/Acct/Id/IBAN")
            or root.findtext(".//Stmt/Acct/Id/Othr/Id")
            or root.findtext(".//DbtrAcct/Id/IBAN")
            or root.findtext(".//DbtrAcct/Id/Othr/Id")
            or root.findtext(".//CdtrAcct/Id/IBAN")
            or root.findtext(".//CdtrAcct/Id/Othr/Id")
        )
        if not acct_id:
            self._log_siem_parse_failure("camt.053", "Missing statement account identifier")
            raise ValueError("ISO 20022 XML parsing failed: missing statement account identifier in camt.053")

        acct_country = FinancialMessageParser.extract_country_code(acct_id)
        entries = root.findall(".//Stmt/Ntry")
        results: list[NormalizedTransaction] = []

        for idx, ntry in enumerate(entries):
            amt_elem = ntry.find(".//Amt")
            if amt_elem is None or not amt_elem.text or not amt_elem.text.strip():
                self._log_siem_parse_failure("camt.053", f"Missing amount in entry index {idx}")
                raise ValueError(f"ISO 20022 XML parsing failed: statement entry {idx} missing amount")

            try:
                amount = float(amt_elem.text.strip())
                if amount <= 0:
                    raise ValueError("Statement entry amount must be positive")
            except ValueError as err:
                self._log_siem_parse_failure("camt.053", f"Invalid entry amount at index {idx}: {err}")
                raise ValueError(f"ISO 20022 XML parsing failed: statement entry {idx} invalid amount: {err}") from err

            currency = amt_elem.get("Ccy") or "EUR"
            tx_id = ntry.findtext(".//NtryRef") or f"camt053_entry_{idx}"
            entry_acct = (
                ntry.findtext(".//DbtrAcct/Id/IBAN")
                or ntry.findtext(".//DbtrAcct/Id/Othr/Id")
                or acct_id
            )
            counterparty = (
                ntry.findtext(".//CdtrAcct/Id/IBAN")
                or ntry.findtext(".//CdtrAcct/Id/Othr/Id")
                or ntry.findtext(".//NtryDtls/TxDtls/RltdPties/Cdtr/Nm")
                or ntry.findtext(".//NtryDtls/TxDtls/RltdPties/Dbtr/Nm")
                or f"COUNTERPARTY_{idx}"
            )
            entry_country = FinancialMessageParser.extract_country_code(entry_acct) or acct_country
            counterparty_country = FinancialMessageParser.extract_country_code(counterparty) or entry_country

            tx = NormalizedTransaction(
                transaction_id=tx_id,
                account_id=entry_acct,
                counterparty_account_id=counterparty,
                amount=amount,
                currency=currency,
                timestamp=datetime.now(UTC),
                merchant_category_code="6012",
                origin_country=entry_country,
                destination_country=counterparty_country,
                channel_type="ISO20022_CAMT053",
            )
            results.append(tx)
            self._parsed_queue.append(tx)

        return results

    @retry_connector()
    def parse_pacs002_xml(self, xml_content: str) -> NormalizedTransaction:
        """Parses an ISO 20022 pacs.002.001.10 Payment Status Report XML string."""
        self.validate_xml_schema(xml_content, "pacs.002.001.10.xsd")
        root = ET.fromstring(xml_content.strip())  # nosec B314

        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        msg_id = root.findtext(".//GrpHdr/MsgId") or f"pacs002_{int(datetime.now(UTC).timestamp())}"
        status = root.findtext(".//OrgnlPmtInfAndSts/TxInfAndSts/TxSts") or root.findtext(".//TxInfAndSts/TxSts") or "ACTC"
        orig_msg_id = root.findtext(".//OrgnlPmtInfAndSts/OrgnlPmtInfId") or root.findtext(".//OrgnlGrpInfAndSts/OrgnlMsgId") or "ORIG_UNKNOWN"

        amt_elem = root.find(".//OrgnlTxRef/Amt")
        if amt_elem is None:
            amt_elem = root.find(".//Amt")
        amount = 1.0
        if amt_elem is not None and amt_elem.text and amt_elem.text.strip():
            try:
                parsed_amt = float(amt_elem.text.strip())
                if parsed_amt > 0:
                    amount = parsed_amt
            except ValueError:
                amount = 1.0

        tx = NormalizedTransaction(
            transaction_id=msg_id,
            account_id=orig_msg_id,
            counterparty_account_id=f"STATUS_{status}",
            amount=amount,
            currency="EUR",
            timestamp=datetime.now(UTC),
            merchant_category_code="6012",
            origin_country="XX",
            destination_country="XX",
            channel_type="ISO20022_PACS002",
        )
        self._parsed_queue.append(tx)
        return tx

    def consume_stream(self) -> Generator[NormalizedTransaction, None, None]:
        """Yields transactions from parsed message queue."""
        while self._parsed_queue:
            yield self._parsed_queue.pop(0)

    def parse_batch(self, payload: Any, strict: bool = False) -> list[NormalizedTransaction]:
        """Parses batch of XML/SWIFT message strings with error tracking and optional strict mode."""
        if isinstance(payload, list):
            results: list[NormalizedTransaction] = []
            for item in payload:
                if isinstance(item, str):
                    try:
                        if "camt.053" in item or "BkToCstmrStmt" in item:
                            results.extend(self.parse_camt053_xml(item))
                        elif "pain.001" in item or "CstmrCdtTrfInitn" in item:
                            results.append(self.parse_pain001_xml(item))
                        elif "pacs.002" in item or "FIToFIPmtStsRpt" in item:
                            results.append(self.parse_pacs002_xml(item))
                        elif "pacs.008" in item or "FIToFICstmrCdtTrf" in item or "<Document" in item:
                            results.append(self.parse_pacs008_xml(item))
                        elif ":20:" in item or ":32A:" in item:
                            results.append(self.parse_swift_mt103(item))
                        else:
                            raise ValueError(f"Unrecognized message format in batch item: {item[:64]}")
                    except Exception as err:
                        self._failed_parse_count += 1
                        self._log_siem_parse_failure("BATCH_ITEM", str(err))
                        if strict:
                            raise
            return results
        elif isinstance(payload, str):
            try:
                if "camt.053" in payload or "BkToCstmrStmt" in payload:
                    return self.parse_camt053_xml(payload)
                elif "pain.001" in payload or "CstmrCdtTrfInitn" in payload:
                    return [self.parse_pain001_xml(payload)]
                elif "pacs.002" in payload or "FIToFIPmtStsRpt" in payload:
                    return [self.parse_pacs002_xml(payload)]
                elif "pacs.008" in payload or "FIToFICstmrCdtTrf" in payload or "<Document" in payload:
                    return [self.parse_pacs008_xml(payload)]
                elif ":20:" in payload or ":32A:" in payload:
                    return [self.parse_swift_mt103(payload)]
                else:
                    raise ValueError(f"Unrecognized message format in payload string: {payload[:64]}")
            except Exception as err:
                self._failed_parse_count += 1
                self._log_siem_parse_failure("BATCH_STRING", str(err))
                raise
        return []

    def anonymize_transaction(
        self, tx: NormalizedTransaction, salt: str = "cf_secagg_salt_2026"
    ) -> NormalizedTransaction:
        """Derive zero-PII privacy-preserving NormalizedTransaction using salted HMAC-SHA256 account hashing."""
        hashed_debtor = hmac.new(
            salt.encode("utf-8"), tx.account_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        hashed_creditor = hmac.new(
            salt.encode("utf-8"), tx.counterparty_account_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return tx.model_copy(
            update={
                "account_id": hashed_debtor,
                "counterparty_account_id": hashed_creditor,
            }
        )
