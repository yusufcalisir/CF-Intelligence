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
    dET = None
    DefusedXmlException = Exception
    DTDForbidden = Exception
    EntitiesForbidden = Exception
    ExternalReferenceForbidden = Exception


class FinancialMessageParserError(Exception):
    """Raised when parsing fails or input is malformed."""

    pass


def _safe_parse_xml(xml_content: str) -> ET.Element:
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
            return dET.fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        return ET.fromstring(content)  # nosec B314
    except (DefusedXmlException, DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as exc:
        raise FinancialMessageParserError(f"XML security validation failure (disallowed entities): {exc}") from exc
    except (ET.ParseError, ValueError) as exc:
        raise FinancialMessageParserError(f"Invalid XML content: {exc}") from exc


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

        return {
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
                return cls.parse_sepa_credit_transfer(trimmed)
            except FinancialMessageParserError as sepa_exc:
                if "forbidden" in str(sepa_exc).lower() or "disallowed" in str(sepa_exc).lower() or "xxe" in str(sepa_exc).lower():
                    raise
                if ":32A:" in trimmed or trimmed.startswith("{"):
                    return cls.parse_swift_mt103(trimmed)
                raise FinancialMessageParserError("Unable to recognize or parse financial message standard") from sepa_exc

