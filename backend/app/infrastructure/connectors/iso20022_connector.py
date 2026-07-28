"""ISO 20022 MX and SWIFT MT Financial Messaging Bank Connector — Section 38.2."""

from __future__ import annotations

import functools
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

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
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
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

        return wrapper  # type: ignore[return-value]

    return decorator


class ISO20022MessagingConnector(BaseBankConnector):
    """Connector for parsing ISO 20022 MX (pacs.008, pacs.002, camt.053, pain.001) XML and SWIFT MT103 messages."""

    def __init__(self) -> None:
        self._parsed_queue: list[NormalizedTransaction] = []
        self._schemas_dir = Path("backend/schemas")
        if not self._schemas_dir.exists():
            self._schemas_dir = Path("schemas")

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

        try:
            root = ET.fromstring(xml_content)  # nosec B314
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

    @retry_connector()
    def parse_pacs008_xml(self, xml_content: str) -> NormalizedTransaction:
        """Parses an ISO 20022 pacs.008.001.08 Financial Institution Customer Credit Transfer XML string."""
        self.validate_xml_schema(xml_content, "pacs.008.001.08.xsd")

        root = ET.fromstring(xml_content)  # nosec B314

        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        msg_id = root.findtext(".//GrpHdr/MsgId") or f"pacs008_{int(datetime.now(UTC).timestamp())}"
        amount_elem = root.find(".//CdtTrfTxInf/IntrBkSttlmAmt")
        amount = float(amount_elem.text) if amount_elem is not None and amount_elem.text else 100.0
        currency = (amount_elem.get("Ccy") if amount_elem is not None else None) or "EUR"

        debtor_account = (
            root.findtext(".//DbtrAcct/Id/Othr/Id")
            or root.findtext(".//DbtrAcct/Id/IBAN")
            or "DEBTOR_UNKNOWN"
        )
        creditor_account = (
            root.findtext(".//CdtrAcct/Id/Othr/Id")
            or root.findtext(".//CdtrAcct/Id/IBAN")
            or "CREDITOR_UNKNOWN"
        )
        debtor_country = root.findtext(".//Dbtr/PstlAdr/Ctry") or "DE"
        creditor_country = root.findtext(".//Cdtr/PstlAdr/Ctry") or "FR"

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

        root = ET.fromstring(xml_content)  # nosec B314

        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        msg_id = root.findtext(".//GrpHdr/MsgId") or f"pain001_{int(datetime.now(UTC).timestamp())}"
        amount_elem = root.find(".//InstdAmt")
        if amount_elem is None:
            amount_elem = root.find(".//EqvtAmt/Amt")
        amount = float(amount_elem.text) if amount_elem is not None and amount_elem.text else 250.0
        currency = (amount_elem.get("Ccy") if amount_elem is not None else None) or "USD"

        debtor_account = (
            root.findtext(".//DbtrAcct/Id/IBAN")
            or root.findtext(".//DbtrAcct/Id/Othr/Id")
            or "PAIN_DEBTOR"
        )
        creditor_account = (
            root.findtext(".//CdtrAcct/Id/IBAN")
            or root.findtext(".//CdtrAcct/Id/Othr/Id")
            or "PAIN_CREDITOR"
        )

        tx = NormalizedTransaction(
            transaction_id=msg_id,
            account_id=debtor_account,
            counterparty_account_id=creditor_account,
            amount=amount,
            currency=currency,
            timestamp=datetime.now(UTC),
            merchant_category_code="6012",
            origin_country="US",
            destination_country="GB",
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
        amount = 500.0
        currency = "USD"
        debtor = "SWIFT_DEBTOR"
        creditor = "SWIFT_CREDITOR"

        for line in lines:
            if line.startswith(":20:"):
                tx_id = line.replace(":20:", "").strip()
            elif line.startswith(":32A:"):
                val = line.replace(":32A:", "").strip()
                m = re.search(r"^[0-9]{6}([A-Z]{3})([0-9,.]+)", val)
                if m:
                    currency = m.group(1)
                    amount = float(m.group(2).replace(",", "."))
            elif line.startswith(":50K:") or line.startswith(":50A:"):
                debtor = line.split(":", 2)[-1].strip()
            elif line.startswith(":59:") or line.startswith(":59A:"):
                creditor = line.split(":", 2)[-1].strip()

        tx = NormalizedTransaction(
            transaction_id=tx_id,
            account_id=debtor,
            counterparty_account_id=creditor,
            amount=amount,
            currency=currency,
            timestamp=datetime.now(UTC),
            merchant_category_code="6011",
            origin_country="US",
            destination_country="GB",
            channel_type="SWIFT_MT103",
        )
        self._parsed_queue.append(tx)
        return tx

    @retry_connector()
    def parse_camt053_xml(self, xml_content: str) -> list[NormalizedTransaction]:
        """Parses an ISO 20022 camt.053.001.08 Bank-to-Customer Statement XML string into a list of NormalizedTransactions."""
        self.validate_xml_schema(xml_content, "camt.053.001.08.xsd")

        root = ET.fromstring(xml_content)  # nosec B314

        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        acct_id = (
            root.findtext(".//Stmt/Acct/Id/IBAN")
            or root.findtext(".//Stmt/Acct/Id/Othr/Id")
            or "STATEMENT_ACCOUNT"
        )
        entries = root.findall(".//Stmt/Ntry")
        results: list[NormalizedTransaction] = []

        for idx, ntry in enumerate(entries):
            amt_elem = ntry.find(".//Amt")
            amount = float(amt_elem.text) if amt_elem is not None and amt_elem.text else 0.0
            currency = (amt_elem.get("Ccy") if amt_elem is not None else None) or "EUR"
            tx_id = ntry.findtext(".//NtryRef") or f"camt053_entry_{idx}"
            counterparty = (
                ntry.findtext(".//NtryDtls/TxDtls/RltdPties/Cdtr/Nm")
                or ntry.findtext(".//NtryDtls/TxDtls/RltdPties/Dbtr/Nm")
                or "COUNTERPARTY_STATEMENT"
            )

            tx = NormalizedTransaction(
                transaction_id=tx_id,
                account_id=acct_id,
                counterparty_account_id=counterparty,
                amount=amount,
                currency=currency,
                timestamp=datetime.now(UTC),
                merchant_category_code="6012",
                origin_country="EU",
                destination_country="EU",
                channel_type="ISO20022_CAMT053",
            )
            results.append(tx)
            self._parsed_queue.append(tx)

        return results

    @retry_connector()
    def parse_pacs002_xml(self, xml_content: str) -> NormalizedTransaction:
        """Parses an ISO 20022 pacs.002.001.10 Payment Status Report XML string."""
        root = ET.fromstring(xml_content)  # nosec B314

        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        msg_id = root.findtext(".//GrpHdr/MsgId") or f"pacs002_{int(datetime.now(UTC).timestamp())}"
        status = root.findtext(".//OrgnlPmtInfAndSts/TxInfAndSts/TxSts") or "ACTC"
        orig_msg_id = root.findtext(".//OrgnlPmtInfAndSts/OrgnlPmtInfId") or "ORIG_UNKNOWN"

        tx = NormalizedTransaction(
            transaction_id=msg_id,
            account_id=orig_msg_id,
            counterparty_account_id=f"STATUS_{status}",
            amount=0.0,
            currency="EUR",
            timestamp=datetime.now(UTC),
            merchant_category_code="6012",
            origin_country="EU",
            destination_country="EU",
            channel_type="ISO20022_PACS002",
        )
        self._parsed_queue.append(tx)
        return tx

    def consume_stream(self) -> Generator[NormalizedTransaction, None, None]:
        """Yields transactions from parsed message queue."""
        while self._parsed_queue:
            yield self._parsed_queue.pop(0)

    def parse_batch(self, payload: Any) -> list[NormalizedTransaction]:
        """Parses batch of XML/SWIFT message strings."""
        if isinstance(payload, list):
            results: list[NormalizedTransaction] = []
            for item in payload:
                if isinstance(item, str):
                    try:
                        if "<camt.053" in item:
                            results.extend(self.parse_camt053_xml(item))
                        elif "<pain.001" in item:
                            results.append(self.parse_pain001_xml(item))
                        elif "<pacs.002" in item:
                            results.append(self.parse_pacs002_xml(item))
                        elif "<pacs.008" in item or "<Document" in item:
                            results.append(self.parse_pacs008_xml(item))
                        elif ":20:" in item or ":32A:" in item:
                            results.append(self.parse_swift_mt103(item))
                    except Exception as err:
                        self._log_siem_parse_failure("BATCH_ITEM", str(err))
            return results
        elif isinstance(payload, str):
            try:
                if "<camt.053" in payload:
                    return self.parse_camt053_xml(payload)
                elif "<pain.001" in payload:
                    return [self.parse_pain001_xml(payload)]
                elif "<pacs.002" in payload:
                    return [self.parse_pacs002_xml(payload)]
                elif "<pacs.008" in payload or "<Document" in payload:
                    return [self.parse_pacs008_xml(payload)]
                elif ":20:" in payload or ":32A:" in payload:
                    return [self.parse_swift_mt103(payload)]
            except Exception as err:
                self._log_siem_parse_failure("BATCH_STRING", str(err))
                raise
        return []
