"""Financial Message Standard Parsers (ISO 20022, SWIFT MT103, SEPA SCT).

Normalizes message schemas into structured transaction dicts and provides
cryptographic zero-PII privacy transforms and IBAN/BIC format validation.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import xml.etree.ElementTree as ET
from typing import Any

try:
    import defusedxml.ElementTree as dET
    from defusedxml.common import (
        DefusedXmlException,
        DTDForbidden,
        EntitiesForbidden,
        ExternalReferenceForbidden,
    )
except ImportError:  # pragma: no cover
    dET = None  # noqa: N816
    DefusedXmlException = Exception
    DTDForbidden = Exception
    EntitiesForbidden = Exception
    ExternalReferenceForbidden = Exception


class FinancialMessageParserError(Exception):
    """Raised when parsing fails or input is malformed."""

    pass


PACS002_REASON_CODES: dict[str, str] = {
    "AC01": "Incorrect Account Number",
    "AC04": "Closed Account Number",
    "AC06": "Blocked Account",
    "AG01": "Transaction Forbidden",
    "AG02": "Invalid Bank Operation Code",
    "AM04": "Insufficient Funds",
    "AM05": "Duplication",
    "BE04": "Missing Creditor Address",
    "CUST": "Customer Cancellation Requested",
    "DNOR": "Debtor Bank Did Not Respond",
    "FF01": "Invalid File Format",
    "FRAD": "Fraudulent Origin Detected",
    "MD01": "No Valid Mandate / Mandate Cancelled",
    "MD07": "End Customer Deceased",
    "MS02": "Reason Not Specified by Debtor Agent",
    "MS03": "Reason Not Specified by Creditor Agent",
    "RC01": "Bank Identifier Code Invalid",
    "RR01": "Missing Debtor Account or Identification",
    "RR02": "Missing Debtor Name or Address",
    "RR03": "Missing Creditor Name or Address",
    "RR04": "Regulatory Reason Compliance Hold",
    "SL01": "Specific Service Offered by Debtor Agent",
}


def _safe_parse_xml(xml_content: str, strip_namespaces: bool = False) -> ET.Element:
    """Parse XML string with strict XXE, external entity, and DTD expansion protections."""
    if not xml_content or not xml_content.strip():
        raise FinancialMessageParserError("Empty XML content")

    content = xml_content.strip()
    if re.search(r"<!(?:DOCTYPE|ENTITY)", content, re.IGNORECASE):
        raise FinancialMessageParserError(
            "XML parsing rejected: DTD and external entity declarations are strictly forbidden (XXE protection)"
        )

    try:
        if dET is not None:
            root = dET.fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        else:
            root = ET.fromstring(content)  # nosec B314
    except (DefusedXmlException, DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as exc:
        raise FinancialMessageParserError(f"XML security validation failure (disallowed entities): {exc}") from exc
    except (ET.ParseError, ValueError) as exc:
        raise FinancialMessageParserError(f"Invalid XML content: {exc}") from exc

    if strip_namespaces:
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

    return root


class FinancialMessageParser:
    """Ingests, parses, and normalizes standard financial transaction messages."""

    @staticmethod
    def generate_valid_iban(country_code: str, bban: str) -> str:
        """Generate an ISO 13616 compliant IBAN with computed Mod-97 check digits."""
        clean_bban = re.sub(r"[^A-Z0-9]", "", bban.upper())
        clean_cc = country_code.strip().upper()
        if len(clean_cc) != 2 or not clean_cc.isalpha():
            raise ValueError(f"Invalid ISO 3166-1 country code: {country_code}")
        rearranged = clean_bban + clean_cc + "00"
        digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
        check = 98 - (int(digits) % 97)
        return f"{clean_cc}{check:02d}{clean_bban}"

    @staticmethod
    def validate_iban(iban: str) -> bool:
        """Validate an International Bank Account Number (IBAN) using ISO 13616 Mod-97 checksum."""
        if not iban or not isinstance(iban, str):
            return False
        clean_iban = re.sub(r"[\s\-]", "", iban).upper()
        if len(clean_iban) < 15 or len(clean_iban) > 34:
            return False
        if not re.match(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$", clean_iban):
            return False
        # Move first 4 characters to end
        rearranged = clean_iban[4:] + clean_iban[:4]
        # Replace letters with digits (A=10, ..., Z=35)
        digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
        try:
            return int(digits) % 97 == 1
        except ValueError:
            return False

    @staticmethod
    def validate_bic(bic: str) -> bool:
        """Validate a Bank Identifier Code (BIC/SWIFT) using ISO 9362 format."""
        if not bic or not isinstance(bic, str):
            return False
        clean_bic = re.sub(r"[\s\-]", "", bic).upper()
        return bool(re.match(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$", clean_bic))

    @staticmethod
    def extract_country_code(account: str | None, postal_country: str | None = None) -> str:
        """Extract 2-letter ISO 3166-1 alpha-2 country code from postal address or account IBAN."""
        if postal_country and len(postal_country.strip()) == 2 and postal_country.strip().isalpha():
            return postal_country.strip().upper()
        if account:
            clean_acct = re.sub(r"[\s\-/]", "", account)
            if len(clean_acct) >= 2 and clean_acct[:2].isalpha():
                return clean_acct[:2].upper()
        return "XX"

    @staticmethod
    def to_privacy_preserving_features(
        parsed_dict: dict[str, Any], salt: str = "cf_privacy_salt_2026"
    ) -> dict[str, Any]:
        """Transform parsed message into zero-PII privacy-preserving feature record using HMAC-SHA256."""
        sender_raw = str(parsed_dict.get("sender_account") or "")
        receiver_raw = str(parsed_dict.get("receiver_account") or "")

        sender_hash = (
            hmac.new(salt.encode("utf-8"), sender_raw.encode("utf-8"), hashlib.sha256).hexdigest()
            if sender_raw
            else ""
        )
        receiver_hash = (
            hmac.new(salt.encode("utf-8"), receiver_raw.encode("utf-8"), hashlib.sha256).hexdigest()
            if receiver_raw
            else ""
        )

        features: dict[str, Any] = {
            "message_type": parsed_dict.get("message_type", "UNKNOWN"),
            "transaction_id": parsed_dict.get("transaction_id", ""),
            "amount": parsed_dict.get("amount", 0.0),
            "currency": parsed_dict.get("currency", "EUR"),
            "date": parsed_dict.get("date", ""),
            "sender_account_hash": sender_hash,
            "receiver_account_hash": receiver_hash,
            "sender_bic": parsed_dict.get("sender_bic", ""),
            "receiver_bic": parsed_dict.get("receiver_bic", ""),
            "sender_country": parsed_dict.get("sender_country", "XX"),
            "receiver_country": parsed_dict.get("receiver_country", "XX"),
            "remittance_info": "[PROTECTED_PII]" if parsed_dict.get("remittance_info") else "",
        }

        if "mandate_id" in parsed_dict:
            mandate_raw = str(parsed_dict.get("mandate_id") or "")
            features["mandate_id_hash"] = (
                hmac.new(salt.encode("utf-8"), mandate_raw.encode("utf-8"), hashlib.sha256).hexdigest()
                if mandate_raw
                else ""
            )
            features["sequence_type"] = parsed_dict.get("sequence_type", "UNKNOWN")

        if "reason_code" in parsed_dict:
            features["reason_code"] = parsed_dict.get("reason_code", "")
            features["status"] = parsed_dict.get("status", "")

        if "entries_count" in parsed_dict:
            features["entries_count"] = parsed_dict.get("entries_count", 0)
            features["total_credit_amount"] = parsed_dict.get("total_credit_amount", 0.0)
            features["total_debit_amount"] = parsed_dict.get("total_debit_amount", 0.0)

        if "risk_score" in parsed_dict:
            features["risk_score"] = parsed_dict.get("risk_score")
            features["risk_level"] = parsed_dict.get("risk_level")

        return features

    @staticmethod
    def parse_iso_20022_pacs008(xml_content: str) -> dict[str, Any]:
        """Parse ISO 20022 Customer Credit Transfer XML message (pacs.008.001.08)."""
        root = _safe_parse_xml(xml_content)

        # Remove namespaces or resolve them dynamically to prevent lookup issues
        ns = ""
        m = re.match(r"({.*})", root.tag)
        if m:
            ns = m.group(1)

        def find_text(element: ET.Element | None, path: str) -> str:
            if element is None:
                return ""
            parts = []
            for p in path.split("/"):
                if not p or p in (".", ".."):
                    parts.append(p)
                elif ns and not p.startswith(ns):
                    parts.append(f"{ns}{p}")
                else:
                    parts.append(p)
            ns_path = "/".join(parts)
            found = element.find(ns_path)
            return found.text.strip() if found is not None and found.text else ""

        # pacs.008 message structures nested under Document/FIToFICstmrCdtTrf/CdtTrfTxInf
        tx_info = root.find(f".//{ns}CdtTrfTxInf")
        if tx_info is None:
            raise FinancialMessageParserError(
                "CdtTrfTxInf (Credit Transfer Transaction Information) block not found in XML"
            )

        # Amount and Currency
        amt_elem = tx_info.find(f"{ns}IntrBkSttlmAmt")
        if amt_elem is None or not amt_elem.text or not amt_elem.text.strip():
            raise FinancialMessageParserError("IntrBkSttlmAmt (Settlement Amount) is missing")
        try:
            amount = float(amt_elem.text.strip())
            if amount <= 0:
                raise FinancialMessageParserError("Settlement amount must be greater than zero")
        except ValueError as exc:
            raise FinancialMessageParserError(f"Invalid amount value: {exc}") from exc
        currency = amt_elem.attrib.get("Ccy", "EUR")

        # Basic fields
        tx_id = (
            find_text(tx_info, "PmtId/EndToEndId")
            or find_text(tx_info, "PmtId/TxId")
            or "unknown_tx_id"
        )
        settlement_date = find_text(tx_info, "IntrBkSttlmDt")

        # Debtor (Sender)
        dbtr_name = find_text(tx_info, "Dbtr/Nm")
        dbtr_iban = find_text(tx_info, "DbtrAcct/Id/Othr/Id") or find_text(
            tx_info, "DbtrAcct/Id/IBAN"
        )
        dbtr_bic = find_text(tx_info, "DbtrAgt/FinInstnId/BICFI")
        dbtr_country = FinancialMessageParser.extract_country_code(
            dbtr_iban, find_text(tx_info, "Dbtr/PstlAdr/Ctry")
        )

        # Creditor (Receiver)
        cdtr_name = find_text(tx_info, "Cdtr/Nm")
        cdtr_iban = find_text(tx_info, "CdtrAcct/Id/Othr/Id") or find_text(
            tx_info, "CdtrAcct/Id/IBAN"
        )
        cdtr_bic = find_text(tx_info, "CdtrAgt/FinInstnId/BICFI")
        cdtr_country = FinancialMessageParser.extract_country_code(
            cdtr_iban, find_text(tx_info, "Cdtr/PstlAdr/Ctry")
        )

        # Remittance info
        remittance = find_text(tx_info, "RmtInf/Ustrd")

        return {
            "message_type": "ISO20022_PACS008",
            "transaction_id": tx_id,
            "amount": amount,
            "currency": currency,
            "date": settlement_date,
            "sender_name": dbtr_name,
            "sender_account": dbtr_iban,
            "sender_bic": dbtr_bic,
            "sender_country": dbtr_country,
            "receiver_name": cdtr_name,
            "receiver_account": cdtr_iban,
            "receiver_bic": cdtr_bic,
            "receiver_country": cdtr_country,
            "remittance_info": remittance,
        }

    @staticmethod
    def parse_swift_mt103(mt103_content: str) -> dict[str, Any]:
        """Parse SWIFT MT103 Single Customer Credit Transfer message."""
        content = mt103_content.strip()
        if not content:
            raise FinancialMessageParserError("Empty MT103 message content")

        # Find block 4, which contains the transaction details
        block4_match = re.search(r"({4:(.*)-})", content, re.DOTALL)
        body = block4_match.group(2) if block4_match else content

        # Helper to extract tags like :XX:
        def get_tag_value(tag: str) -> str:
            # Match the tag and grab lines until the next tag starting with : or block end
            pattern = rf"(?:^|\n):{tag}:(.*?)(?=\n:\d{{2}}[A-Z]?:|\n-}}|\n$)"
            match = re.search(pattern, body, re.DOTALL)
            return match.group(1).strip() if match else ""

        tx_id = get_tag_value("20")
        if not tx_id:
            tx_id = "unknown_swift_id"

        # Tag 32A contains Date, Currency, Amount (Format: YYMMDDCCYAmount)
        tag32a = get_tag_value("32A")
        amount = 0.0
        currency = "EUR"
        date_str = ""
        if tag32a:
            m = re.match(r"^(\d{6})([A-Z]{3})(.*)$", tag32a)
            if m:
                date_str = "20" + m.group(1)  # Expand to YYYYMMDD format
                currency = m.group(2)
                amt_str = m.group(3).replace(",", ".")
                try:
                    amount = float(amt_str)
                    if amount <= 0:
                        raise FinancialMessageParserError("Settlement amount must be greater than zero")
                except ValueError as exc:
                    raise FinancialMessageParserError(
                        f"Invalid MT103 amount format in 32A: {exc}"
                    ) from exc
            else:
                raise FinancialMessageParserError("Invalid MT103 32A format")
        else:
            raise FinancialMessageParserError("Missing mandatory MT103 field 32A")

        # Tag 50A, 50F, or 50K (Debtor/Ordering Customer)
        tag50 = get_tag_value("50K") or get_tag_value("50A") or get_tag_value("50F")
        sender_account = ""
        sender_name = ""
        if tag50:
            lines = tag50.split("\n")
            if lines[0].startswith("/"):
                sender_account = lines[0][1:]
                sender_name = lines[1] if len(lines) > 1 else ""
            else:
                sender_name = lines[0]
        sender_country = FinancialMessageParser.extract_country_code(sender_account)

        # Tag 59 or 59A (Creditor/Beneficiary)
        tag59 = get_tag_value("59") or get_tag_value("59A")
        receiver_account = ""
        receiver_name = ""
        if tag59:
            lines = tag59.split("\n")
            if lines[0].startswith("/"):
                receiver_account = lines[0][1:]
                receiver_name = lines[1] if len(lines) > 1 else ""
            else:
                receiver_name = lines[0]
        receiver_country = FinancialMessageParser.extract_country_code(receiver_account)

        # Tag 70 (Remittance Info / Details of Payment)
        remittance_info = get_tag_value("70")

        # Tag 57A (Account With Institution BIC)
        receiver_bic = get_tag_value("57A")
        if receiver_country == "XX" and receiver_bic and len(receiver_bic) >= 6:
            bic_country = receiver_bic[4:6].upper()
            if bic_country.isalpha():
                receiver_country = bic_country

        return {
            "message_type": "SWIFT_MT103",
            "transaction_id": tx_id,
            "amount": amount,
            "currency": currency,
            "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            if len(date_str) == 8
            else date_str,
            "sender_name": sender_name,
            "sender_account": sender_account,
            "sender_country": sender_country,
            "receiver_name": receiver_name,
            "receiver_account": receiver_account,
            "receiver_bic": receiver_bic,
            "receiver_country": receiver_country,
            "remittance_info": remittance_info,
        }

    @classmethod
    def parse_sepa_credit_transfer(cls, xml_content: str) -> dict[str, Any]:
        """Parse SEPA Credit Transfer (ISO 20022 pacs.008 or pain.001)."""
        try:
            res = cls.parse_iso_20022_pacs008(xml_content)
            res["message_type"] = "SEPA_SCT"
            return res
        except FinancialMessageParserError:
            root = _safe_parse_xml(xml_content)

            ns = ""
            m = re.match(r"({.*})", root.tag)
            if m:
                ns = m.group(1)

            def find_text(element: ET.Element | None, path: str) -> str:
                if element is None:
                    return ""
                parts = []
                for p in path.split("/"):
                    if not p or p in (".", ".."):
                        parts.append(p)
                    elif ns and not p.startswith(ns):
                        parts.append(f"{ns}{p}")
                    else:
                        parts.append(p)
                ns_path = "/".join(parts)
                found = element.find(ns_path)
                return found.text.strip() if found is not None and found.text else ""

            # Check if pain.001 structure
            tx_info = root.find(f".//{ns}CdtTrfTxInf")
            if tx_info is None:
                raise FinancialMessageParserError(
                    "Could not parse XML payload as SEPA Credit Transfer"
                )

            amt_elem = tx_info.find(f"{ns}Amt/{ns}InstdAmt")
            if amt_elem is None:
                amt_elem = tx_info.find(f"{ns}InstdAmt")
            if amt_elem is None or not amt_elem.text or not amt_elem.text.strip():
                raise FinancialMessageParserError("Instruction Amount is missing in SEPA payload")

            try:
                amount = float(amt_elem.text.strip())
                if amount <= 0:
                    raise FinancialMessageParserError("Instruction amount must be greater than zero")
            except ValueError as exc:
                raise FinancialMessageParserError(f"Invalid SEPA amount format: {exc}") from exc

            currency = amt_elem.attrib.get("Ccy", "EUR")
            tx_id = find_text(tx_info, "PmtId/EndToEndId") or "unknown_sepa_id"

            dbtr_name = find_text(root, ".//Dbtr/Nm")
            dbtr_iban = find_text(root, ".//DbtrAcct/Id/IBAN") or find_text(root, ".//DbtrAcct/Id/Othr/Id")
            dbtr_country = FinancialMessageParser.extract_country_code(dbtr_iban)

            cdtr_name = find_text(tx_info, "Cdtr/Nm")
            cdtr_iban = find_text(tx_info, "CdtrAcct/Id/IBAN") or find_text(tx_info, "CdtrAcct/Id/Othr/Id")
            cdtr_country = FinancialMessageParser.extract_country_code(cdtr_iban)

            return {
                "message_type": "SEPA_SCT",
                "transaction_id": tx_id,
                "amount": amount,
                "currency": currency,
                "date": "",
                "sender_name": dbtr_name,
                "sender_account": dbtr_iban,
                "sender_country": dbtr_country,
                "receiver_name": cdtr_name,
                "receiver_account": cdtr_iban,
                "receiver_country": cdtr_country,
                "remittance_info": find_text(tx_info, "RmtInf/Ustrd"),
            }

    @classmethod
    def parse_iso_20022_camt053(cls, xml_content: str) -> dict[str, Any]:
        """Parse ISO 20022 Bank-to-Customer Statement XML message (camt.053.001.08)."""
        root = _safe_parse_xml(xml_content, strip_namespaces=True)

        stmt = root.find(".//Stmt")
        if stmt is None:
            raise FinancialMessageParserError("Stmt (Statement) block not found in camt.053 XML")

        stmt_id = stmt.findtext("Id") or "unknown_stmt_id"
        stmt_date = stmt.findtext("CreDtTm") or stmt.findtext("CreDt") or ""

        # Account details
        acct_iban = (
            stmt.findtext("Acct/Id/IBAN")
            or stmt.findtext("Acct/Id/Othr/Id")
            or ""
        )
        if not acct_iban:
            raise FinancialMessageParserError("Statement account identifier (IBAN/Other) missing in camt.053")

        acct_currency = stmt.findtext("Acct/Ccy") or "EUR"
        servicer_bic = stmt.findtext("Acct/Svcr/FinInstnId/BICFI") or ""
        acct_country = cls.extract_country_code(acct_iban)

        # Balances
        opening_balance: dict[str, Any] | None = None
        closing_balance: dict[str, Any] | None = None

        for bal in stmt.findall("Bal"):
            bal_type = bal.findtext("Tp/CdOrPrtry/Cd") or bal.findtext("Tp/CdOrPrtry/Prtry") or ""
            amt_elem = bal.find("Amt")
            if amt_elem is not None and amt_elem.text and amt_elem.text.strip():
                try:
                    b_amt = float(amt_elem.text.strip())
                except ValueError:
                    b_amt = 0.0
                b_ccy = amt_elem.attrib.get("Ccy", acct_currency)
                b_ind = bal.findtext("CdtDbtInd") or "CRDT"
                b_date = bal.findtext("Dt/Dt") or bal.findtext("Dt/DtTm") or ""
                bal_dict = {
                    "balance_type": bal_type,
                    "amount": b_amt,
                    "currency": b_ccy,
                    "credit_debit_indicator": b_ind,
                    "date": b_date,
                }
                if bal_type in ("OPBD", "PRCD"):
                    opening_balance = bal_dict
                elif bal_type in ("CLBD", "ITBD"):
                    closing_balance = bal_dict

        # Entries
        entries_list: list[dict[str, Any]] = []
        total_credit = 0.0
        total_debit = 0.0

        for idx, ntry in enumerate(stmt.findall("Ntry")):
            amt_elem = ntry.find("Amt")
            if amt_elem is None or not amt_elem.text or not amt_elem.text.strip():
                raise FinancialMessageParserError(f"Statement entry {idx} missing amount in camt.053")
            try:
                e_amt = float(amt_elem.text.strip())
                if e_amt <= 0:
                    raise FinancialMessageParserError(f"Statement entry {idx} amount must be positive")
            except ValueError as exc:
                raise FinancialMessageParserError(f"Invalid statement entry amount format: {exc}") from exc

            e_ccy = amt_elem.attrib.get("Ccy", acct_currency)
            e_ind = ntry.findtext("CdtDbtInd") or "CRDT"
            e_ref = ntry.findtext("NtryRef") or f"ENTRY_{idx + 1}"
            e_status = ntry.findtext("Sts") or "BOOK"
            e_book_date = ntry.findtext("BookgDt/Dt") or ntry.findtext("BookgDt/DtTm") or ""
            e_val_date = ntry.findtext("ValDt/Dt") or ntry.findtext("ValDt/DtTm") or ""

            if e_ind.upper() == "CRDT":
                total_credit += e_amt
            else:
                total_debit += e_amt

            # Counterparty / Related Parties details
            tx_dtls = ntry.find(".//TxDtls")
            cp_name = ""
            cp_iban = ""
            cp_bic = ""
            e2e_id = ""
            rmt_info = ""

            if tx_dtls is not None:
                e2e_id = (
                    tx_dtls.findtext("Refs/EndToEndId")
                    or tx_dtls.findtext("Refs/TxId")
                    or ""
                )
                rmt_info = (
                    tx_dtls.findtext("RmtInf/Ustrd")
                    or tx_dtls.findtext(".//RmtInf/Strd/CdtrRefInf/Ref")
                    or ""
                )
                if e_ind.upper() == "CRDT":
                    cp_name = tx_dtls.findtext("RltdPties/Dbtr/Nm") or tx_dtls.findtext(".//Dbtr/Nm") or ""
                    cp_iban = (
                        tx_dtls.findtext("RltdPties/DbtrAcct/Id/IBAN")
                        or tx_dtls.findtext("RltdPties/DbtrAcct/Id/Othr/Id")
                        or ""
                    )
                    cp_bic = tx_dtls.findtext("RltdAgts/DbtrAgt/FinInstnId/BICFI") or ""
                else:
                    cp_name = tx_dtls.findtext("RltdPties/Cdtr/Nm") or tx_dtls.findtext(".//Cdtr/Nm") or ""
                    cp_iban = (
                        tx_dtls.findtext("RltdPties/CdtrAcct/Id/IBAN")
                        or tx_dtls.findtext("RltdPties/CdtrAcct/Id/Othr/Id")
                        or ""
                    )
                    cp_bic = tx_dtls.findtext("RltdAgts/CdtrAgt/FinInstnId/BICFI") or ""

            cp_country = cls.extract_country_code(cp_iban) if cp_iban else acct_country

            entries_list.append({
                "entry_reference": e_ref,
                "amount": e_amt,
                "currency": e_ccy,
                "credit_debit_indicator": e_ind,
                "status": e_status,
                "booking_date": e_book_date,
                "value_date": e_val_date,
                "counterparty_name": cp_name,
                "counterparty_iban": cp_iban,
                "counterparty_bic": cp_bic,
                "counterparty_country": cp_country,
                "end_to_end_id": e2e_id,
                "remittance_info": rmt_info,
            })

        return {
            "message_type": "ISO20022_CAMT053",
            "statement_id": stmt_id,
            "transaction_id": stmt_id,
            "amount": round(total_credit + total_debit, 2),
            "currency": acct_currency,
            "date": stmt_date,
            "account_iban": acct_iban,
            "account_currency": acct_currency,
            "servicer_bic": servicer_bic,
            "statement_date": stmt_date,
            "sender_account": acct_iban,
            "receiver_account": acct_iban,
            "sender_country": acct_country,
            "receiver_country": acct_country,
            "opening_balance": opening_balance,
            "closing_balance": closing_balance,
            "entries_count": len(entries_list),
            "total_credit_amount": round(total_credit, 2),
            "total_debit_amount": round(total_debit, 2),
            "entries": entries_list,
        }

    @classmethod
    def parse_iso_20022_pacs002(cls, xml_content: str) -> dict[str, Any]:
        """Parse ISO 20022 Payment Status Report XML message (pacs.002.001.10)."""
        root = _safe_parse_xml(xml_content, strip_namespaces=True)

        msg_id = root.findtext(".//GrpHdr/MsgId") or "unknown_status_msg_id"
        cre_dt_tm = root.findtext(".//GrpHdr/CreDtTm") or ""

        orig_msg_id = (
            root.findtext(".//OrgnlGrpInfAndSts/OrgnlMsgId")
            or root.findtext(".//OrgnlPmtInfAndSts/OrgnlPmtInfId")
            or ""
        )
        orig_msg_nm_id = root.findtext(".//OrgnlGrpInfAndSts/OrgnlMsgNmId") or ""
        grp_sts = root.findtext(".//OrgnlGrpInfAndSts/GrpSts") or ""

        tx_info = root.find(".//TxInfAndSts")
        if tx_info is None:
            tx_info = root.find(".//OrgnlPmtInfAndSts/TxInfAndSts")

        tx_status = (
            (tx_info.findtext("TxSts") if tx_info is not None else "")
            or grp_sts
            or "ACCP"
        )
        sts_id = (tx_info.findtext("StsId") if tx_info is not None else "") or msg_id
        orig_e2e_id = (tx_info.findtext("OrgnlEndToEndId") if tx_info is not None else "") or ""
        orig_tx_id = (tx_info.findtext("OrgnlTxId") if tx_info is not None else "") or ""

        reason_code = ""
        addtl_inf = ""
        if tx_info is not None:
            reason_code = (
                tx_info.findtext("StsRsnInf/Rsn/Cd")
                or tx_info.findtext("StsRsnInf/Rsn/Prtry")
                or ""
            )
            addtl_inf = tx_info.findtext("StsRsnInf/AddtlInf") or ""
        if not reason_code and root.find(".//OrgnlGrpInfAndSts/StsRsnInf") is not None:
            reason_code = (
                root.findtext(".//OrgnlGrpInfAndSts/StsRsnInf/Rsn/Cd")
                or root.findtext(".//OrgnlGrpInfAndSts/StsRsnInf/Rsn/Prtry")
                or ""
            )
            addtl_inf = root.findtext(".//OrgnlGrpInfAndSts/StsRsnInf/AddtlInf") or ""

        reason_desc = PACS002_REASON_CODES.get(
            reason_code, "Reason code not mapped or narrative" if reason_code else ""
        )

        orig_ref = tx_info.find("OrgnlTxRef") if tx_info is not None else root.find(".//OrgnlTxRef")
        amount = 0.0
        currency = "EUR"
        dbtr_name = ""
        dbtr_iban = ""
        dbtr_bic = ""
        cdtr_name = ""
        cdtr_iban = ""
        cdtr_bic = ""

        if orig_ref is not None:
            amt_elem = orig_ref.find("Amt")
            if amt_elem is None:
                amt_elem = orig_ref.find("Amt/InstdAmt")
            if amt_elem is None:
                amt_elem = orig_ref.find("IntrBkSttlmAmt")
            if amt_elem is not None and amt_elem.text and amt_elem.text.strip():
                try:
                    amount = float(amt_elem.text.strip())
                except ValueError:
                    amount = 0.0
                currency = amt_elem.attrib.get("Ccy", "EUR")

            dbtr_name = orig_ref.findtext("Dbtr/Nm") or ""
            dbtr_iban = (
                orig_ref.findtext("DbtrAcct/Id/IBAN")
                or orig_ref.findtext("DbtrAcct/Id/Othr/Id")
                or ""
            )
            dbtr_bic = orig_ref.findtext("DbtrAgt/FinInstnId/BICFI") or ""

            cdtr_name = orig_ref.findtext("Cdtr/Nm") or ""
            cdtr_iban = (
                orig_ref.findtext("CdtrAcct/Id/IBAN")
                or orig_ref.findtext("CdtrAcct/Id/Othr/Id")
                or ""
            )
            cdtr_bic = orig_ref.findtext("CdtrAgt/FinInstnId/BICFI") or ""

        dbtr_country = cls.extract_country_code(dbtr_iban)
        cdtr_country = cls.extract_country_code(cdtr_iban)

        is_rejected = tx_status.upper() in ("RJCT", "CANC", "PDNG_REJ")

        return {
            "message_type": "ISO20022_PACS002",
            "status_message_id": msg_id,
            "transaction_id": orig_e2e_id or orig_tx_id or sts_id,
            "status": tx_status,
            "is_rejected": is_rejected,
            "reason_code": reason_code,
            "reason_description": reason_desc,
            "additional_info": addtl_inf,
            "amount": amount,
            "currency": currency,
            "date": cre_dt_tm,
            "original_msg_id": orig_msg_id,
            "original_msg_name": orig_msg_nm_id,
            "original_end_to_end_id": orig_e2e_id,
            "original_tx_id": orig_tx_id,
            "sender_name": dbtr_name,
            "sender_account": dbtr_iban,
            "sender_bic": dbtr_bic,
            "sender_country": dbtr_country,
            "receiver_name": cdtr_name,
            "receiver_account": cdtr_iban,
            "receiver_bic": cdtr_bic,
            "receiver_country": cdtr_country,
            "remittance_info": f"Status: {tx_status} Reason: {reason_code or 'NONE'}",
        }

    @classmethod
    def parse_iso_20022_pacs003(cls, xml_content: str) -> dict[str, Any]:
        """Parse ISO 20022 Customer Direct Debit XML message (pacs.003.001.08)."""
        root = _safe_parse_xml(xml_content, strip_namespaces=True)

        tx_info = root.find(".//DrctDbtTxInf")
        if tx_info is None:
            raise FinancialMessageParserError(
                "DrctDbtTxInf (Direct Debit Transaction Information) block not found in XML"
            )

        amt_elem = tx_info.find("IntrBkSttlmAmt")
        if amt_elem is None:
            amt_elem = tx_info.find("InstdAmt")
        if amt_elem is None or not amt_elem.text or not amt_elem.text.strip():
            raise FinancialMessageParserError("Settlement amount is missing in pacs.003 payload")
        try:
            amount = float(amt_elem.text.strip())
            if amount <= 0:
                raise FinancialMessageParserError("Direct debit amount must be greater than zero")
        except ValueError as exc:
            raise FinancialMessageParserError(f"Invalid direct debit amount format: {exc}") from exc
        currency = amt_elem.attrib.get("Ccy", "EUR")

        e2e_id = (
            tx_info.findtext("PmtId/EndToEndId")
            or tx_info.findtext("PmtId/TxId")
            or "unknown_pacs003_id"
        )
        settlement_date = tx_info.findtext("IntrBkSttlmDt") or tx_info.findtext("ReqdColltnDt") or ""
        seq_type = tx_info.findtext("PmtTpInf/SeqTp") or "RCUR"

        mandate_id = tx_info.findtext("DrctDbtTx/MndtRltdInf/MndtId") or ""
        dt_of_sgntr = tx_info.findtext("DrctDbtTx/MndtRltdInf/DtOfSgntr") or ""
        scheme_id = tx_info.findtext("DrctDbtTx/CdtrSchmeId/Id/PrvtId/Othr/Id") or ""

        cdtr_name = tx_info.findtext("Cdtr/Nm") or ""
        cdtr_iban = (
            tx_info.findtext("CdtrAcct/Id/IBAN")
            or tx_info.findtext("CdtrAcct/Id/Othr/Id")
            or ""
        )
        cdtr_bic = tx_info.findtext("CdtrAgt/FinInstnId/BICFI") or ""
        cdtr_country = cls.extract_country_code(cdtr_iban, tx_info.findtext("Cdtr/PstlAdr/Ctry"))

        dbtr_name = tx_info.findtext("Dbtr/Nm") or ""
        dbtr_iban = (
            tx_info.findtext("DbtrAcct/Id/IBAN")
            or tx_info.findtext("DbtrAcct/Id/Othr/Id")
            or ""
        )
        dbtr_bic = tx_info.findtext("DbtrAgt/FinInstnId/BICFI") or ""
        dbtr_country = cls.extract_country_code(dbtr_iban, tx_info.findtext("Dbtr/PstlAdr/Ctry"))

        remittance = tx_info.findtext("RmtInf/Ustrd") or ""

        return {
            "message_type": "ISO20022_PACS003",
            "transaction_id": e2e_id,
            "amount": amount,
            "currency": currency,
            "date": settlement_date,
            "sequence_type": seq_type.upper(),
            "mandate_id": mandate_id,
            "mandate_signature_date": dt_of_sgntr,
            "creditor_scheme_id": scheme_id,
            "sender_name": dbtr_name,
            "sender_account": dbtr_iban,
            "sender_bic": dbtr_bic,
            "sender_country": dbtr_country,
            "receiver_name": cdtr_name,
            "receiver_account": cdtr_iban,
            "receiver_bic": cdtr_bic,
            "receiver_country": cdtr_country,
            "remittance_info": remittance,
        }

    @classmethod
    def score_direct_debit_risk(cls, pacs003_data: dict[str, Any]) -> dict[str, Any]:
        """Evaluate heuristic fraud risk for SEPA / ISO 20022 Direct Debit pulls (unauthorized debit protection)."""
        score_points = 0
        risk_factors: list[str] = []

        amount = float(pacs003_data.get("amount", 0.0))
        seq_type = str(pacs003_data.get("sequence_type", "RCUR")).upper()
        mandate_id = str(pacs003_data.get("mandate_id") or "").strip()
        dt_of_sgntr = str(pacs003_data.get("mandate_signature_date") or "").strip()
        settlement_date = str(pacs003_data.get("date") or "").strip()
        sender_iban = str(pacs003_data.get("sender_account") or "").strip()
        receiver_iban = str(pacs003_data.get("receiver_account") or "").strip()
        sender_bic = str(pacs003_data.get("sender_bic") or "").strip()
        sender_country = str(pacs003_data.get("sender_country", "XX")).upper()
        receiver_country = str(pacs003_data.get("receiver_country", "XX")).upper()

        if not mandate_id:
            score_points += 35
            risk_factors.append("Missing mandate identification in direct debit instruction")
        elif len(mandate_id) < 4:
            score_points += 15
            risk_factors.append("Suspiciously short mandate identifier")

        if seq_type == "OOFF":
            score_points += 15
            risk_factors.append("One-Off direct debit sequence type has elevated dispute frequency")
        elif seq_type == "FRST":
            score_points += 10
            risk_factors.append("First direct debit collection under newly presented mandate")
            if dt_of_sgntr and settlement_date and dt_of_sgntr == settlement_date:
                score_points += 20
                risk_factors.append("Same-day mandate creation and execution without debtor cooling period")
        elif seq_type not in ("RCUR", "FNAL"):
            score_points += 15
            risk_factors.append(f"Unrecognized sequence type: {seq_type}")

        if sender_country != receiver_country and sender_country != "XX" and receiver_country != "XX":
            score_points += 20
            risk_factors.append(f"Cross-border direct debit pull ({sender_country} -> {receiver_country})")
        if sender_country == "XX" or receiver_country == "XX":
            score_points += 10
            risk_factors.append("Unverified jurisdiction in debtor or creditor accounts")

        if amount > 10000.0:
            score_points += 25
            risk_factors.append(f"High-value direct debit pull ({amount:,.2f} EUR) exceeding standard retail threshold")
        elif amount > 2500.0:
            score_points += 12
            risk_factors.append(f"Elevated direct debit pull ({amount:,.2f} EUR)")

        if sender_iban and not cls.validate_iban(sender_iban):
            score_points += 30
            risk_factors.append("Debtor IBAN failed ISO 13616 Mod-97 checksum validation")
        if receiver_iban and not cls.validate_iban(receiver_iban):
            score_points += 25
            risk_factors.append("Creditor IBAN failed ISO 13616 Mod-97 checksum validation")
        if sender_bic and not cls.validate_bic(sender_bic):
            score_points += 10
            risk_factors.append("Debtor BIC format validation failed")

        risk_score = round(min(1.0, max(0.0, score_points / 100.0)), 3)

        if risk_score < 0.35:
            risk_level = "LOW"
            recommended_action = "ALLOW"
        elif risk_score < 0.70:
            risk_level = "MEDIUM"
            recommended_action = "FLAG_FOR_CONFIRMATION"
        else:
            risk_level = "HIGH"
            recommended_action = "REJECT_AND_HOLD"

        return {
            "transaction_id": pacs003_data.get("transaction_id", "unknown_tx_id"),
            "risk_score": risk_score,
            "risk_level": risk_level,
            "recommended_action": recommended_action,
            "score_points": score_points,
            "risk_factors": risk_factors,
            "sequence_type": seq_type,
            "amount": amount,
            "currency": pacs003_data.get("currency", "EUR"),
            "debtor_country": sender_country,
            "creditor_country": receiver_country,
            "mandate_id": mandate_id,
        }

    @classmethod
    def parse_message(cls, content: str, message_type: str = "auto") -> dict[str, Any]:
        """Automatically identify and parse standard financial messages (ISO 20022, SEPA, SWIFT MT103)."""
        if not content or not content.strip():
            raise FinancialMessageParserError("Empty financial message payload")

        trimmed = content.strip()

        # Immediate XXE & DTD injection defense for all XML-like financial payloads
        if re.search(r"<!(?:DOCTYPE|ENTITY)", trimmed, re.IGNORECASE):
            raise FinancialMessageParserError(
                "XML parsing rejected: DTD and external entity declarations are strictly forbidden (XXE protection)"
            )

        msg_type_lower = (message_type or "auto").lower()

        if msg_type_lower in ("mt103", "swift_mt103") or (
            msg_type_lower == "auto" and (trimmed.startswith("{1:") or ":32A:" in trimmed)
        ):
            return cls.parse_swift_mt103(trimmed)

        if msg_type_lower in ("camt.053", "camt053", "camt_053", "bank_statement") or (
            msg_type_lower == "auto" and ("camt.053" in trimmed or "BkToCstmrStmt" in trimmed)
        ):
            return cls.parse_iso_20022_camt053(trimmed)

        if msg_type_lower in ("pacs.002", "pacs002", "pacs_002", "payment_status") or (
            msg_type_lower == "auto" and ("pacs.002" in trimmed or "FIToFIPmtStsRpt" in trimmed)
        ):
            return cls.parse_iso_20022_pacs002(trimmed)

        if msg_type_lower in ("pacs.003", "pacs003", "pacs_003", "direct_debit") or (
            msg_type_lower == "auto" and ("pacs.003" in trimmed or "FIToFICstmrDrctDbt" in trimmed)
        ):
            return cls.parse_iso_20022_pacs003(trimmed)

        if msg_type_lower in ("pain.001", "pain001", "sepa", "sepa_sct") or (
            msg_type_lower == "auto" and ("pain.001" in trimmed or "CstmrCdtTrfInitn" in trimmed)
        ):
            return cls.parse_sepa_credit_transfer(trimmed)

        if msg_type_lower in ("pacs.008", "pacs008", "iso20022"):
            return cls.parse_iso_20022_pacs008(trimmed)

        try:
            return cls.parse_iso_20022_pacs008(trimmed)
        except FinancialMessageParserError as exc:
            if "forbidden" in str(exc).lower() or "disallowed" in str(exc).lower() or "xxe" in str(exc).lower():
                raise
            try:
                return cls.parse_iso_20022_camt053(trimmed)
            except FinancialMessageParserError as camt_exc:
                if "forbidden" in str(camt_exc).lower() or "disallowed" in str(camt_exc).lower() or "xxe" in str(camt_exc).lower():
                    raise
                try:
                    return cls.parse_iso_20022_pacs002(trimmed)
                except FinancialMessageParserError as pacs2_exc:
                    if "forbidden" in str(pacs2_exc).lower() or "disallowed" in str(pacs2_exc).lower() or "xxe" in str(pacs2_exc).lower():
                        raise
                    try:
                        return cls.parse_iso_20022_pacs003(trimmed)
                    except FinancialMessageParserError as pacs3_exc:
                        if "forbidden" in str(pacs3_exc).lower() or "disallowed" in str(pacs3_exc).lower() or "xxe" in str(pacs3_exc).lower():
                            raise
                        try:
                            return cls.parse_sepa_credit_transfer(trimmed)
                        except FinancialMessageParserError as sepa_exc:
                            if "forbidden" in str(sepa_exc).lower() or "disallowed" in str(sepa_exc).lower() or "xxe" in str(sepa_exc).lower():
                                raise
                            if ":32A:" in trimmed or trimmed.startswith("{"):
                                return cls.parse_swift_mt103(trimmed)
                            raise FinancialMessageParserError("Unable to recognize or parse financial message standard") from sepa_exc

