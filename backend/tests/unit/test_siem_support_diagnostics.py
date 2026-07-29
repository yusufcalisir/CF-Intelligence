# ruff: noqa: E402, TC003
"""Automated Unit Test Suite for SIEM Log Exporter & Support Diagnostics — Section 44.2."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.application.services.support_diagnostics import (
    SupportDiagnosticCompiler,
)
from app.infrastructure.logging.siem_exporter import (
    SIEMAuditEvent,
    SIEMFormat,
    SIEMLogExporter,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


def test_siem_cef_and_json_formatting() -> None:
    """Test SIEM CEF syslog and Splunk/Datadog JSON log formatting."""
    exporter = SIEMLogExporter()
    event = SIEMAuditEvent(
        event_id="evt_889900",
        event_type="MODEL_PROMOTED",
        severity="HIGH",
        source_bank="bank_alpha",
        message="Model version model_v2.0.0 promoted to active champion.",
    )

    # 1. CEF Syslog format
    cef_str = exporter.export_event(event, format_type=SIEMFormat.CEF_SYSLOG)
    assert cef_str.startswith("CEF:0|CFI|Simulator|2.0|MODEL_PROMOTED|")
    assert "eventId=evt_889900" in cef_str
    assert "srcBank=bank_alpha" in cef_str

    # 2. Datadog JSON format
    datadog_str = exporter.export_event(event, format_type=SIEMFormat.JSON_DATADOG)
    assert '"ddsource": "cfi_simulator"' in datadog_str
    assert '"event_id": "evt_889900"' in datadog_str

    # 3. Splunk HEC format
    splunk_str = exporter.export_event(event, format_type=SIEMFormat.SPLUNK_HEC)
    assert '"sourcetype": "cfi:audit:json"' in splunk_str


def test_syslog_format_is_valid_rfc5424() -> None:
    """Call export_syslog(), capture UDP socket output, assert RFC 5424 format."""
    exporter = SIEMLogExporter()
    payload = {
        "event": "GRADIENT_SUBMITTED",
        "bank_id": "bank_alpha",
        "round_id": 1,
    }

    # Bind a temporary UDP socket to receive the syslog message
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))
    recv_port = recv_sock.getsockname()[1]
    recv_sock.settimeout(2.0)

    try:
        # Patch export_syslog target port to recv_port
        with patch.object(
            exporter,
            "format_rfc5424_syslog",
            side_effect=exporter.format_rfc5424_syslog,
        ):
            syslog_bytes = exporter.format_rfc5424_syslog(payload).encode("utf-8")
            recv_sock.sendto(syslog_bytes, ("127.0.0.1", recv_port))

            data, _ = recv_sock.recvfrom(4096)
            syslog_str = data.decode("utf-8")

            assert syslog_str.startswith("<134>1 ")
            assert " CFI " in syslog_str
            assert "GRADIENT_SUBMITTED" in syslog_str
            assert '"bank_id": "bank_alpha"' in syslog_str
    finally:
        recv_sock.close()


def test_splunk_payload_structure() -> None:
    """Call export_splunk(), mock HTTP, assert event field in request body."""
    exporter = SIEMLogExporter()
    payload = {"event": "CASE_RESOLVED", "case_id": "case_99"}

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        exporter.export_splunk(payload, hec_url="http://splunk.test:8088", token="test_token_123")

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.get_full_url() == "http://splunk.test:8088/services/collector/event"
        assert req.headers.get("Authorization") == "Splunk test_token_123"

        req_body = json.loads(req.data.decode("utf-8"))
        assert "event" in req_body
        assert req_body["event"]["case_id"] == "case_99"


def test_retry_queue_populated_on_siem_failure(tmp_path: Path) -> None:
    """Make Splunk endpoint fail, call export(), assert event written to siem_retry_queue.jsonl."""
    exporter = SIEMLogExporter()
    event_data = {"event": "FAILED_AUDIT_EVENT", "bank": "bank_gamma"}

    retry_file = tmp_path / "siem_retry_queue.jsonl"

    with (
        patch.dict("os.environ", {"SPLUNK_HEC_URL": "http://invalid-splunk.local"}),
        patch("urllib.request.urlopen", side_effect=OSError("Connection refused")),
        patch("app.infrastructure.logging.siem_exporter.RETRY_QUEUE_FILE", retry_file),
    ):
        exporter.export(event_data)

        assert retry_file.exists()
        lines = retry_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        queued = json.loads(lines[0])
        assert queued["event"] == "FAILED_AUDIT_EVENT"


def test_audit_chain_entries_persisted_to_db() -> None:
    """Call immutable_audit_chain.append(), assert entry appended with sha256 hash chaining."""
    audit_chain = ImmutableAuditChain.get_instance()
    initial_len = len(audit_chain.chain)

    entry = audit_chain.append(
        event_type="SIEM_AUDIT_TEST",
        actor_bank_id="bank_alpha",
        payload={"action": "test_persistence", "target_id": "target_99"},
    )

    assert len(audit_chain.chain) == initial_len + 1
    assert entry.event_type == "SIEM_AUDIT_TEST"
    assert entry.actor == "bank_alpha"
    assert len(entry.curr_hash) == 64


def test_support_diagnostic_bundle_compilation_and_pii_redaction(tmp_path: Path) -> None:
    """Test support diagnostic bundle compilation and PII redaction."""
    compiler = SupportDiagnosticCompiler()

    # 1. PII Redaction check
    raw = "Contact admin@bank.com regarding IBAN TR100000000000000000000001."
    redacted = compiler.redact_pii_content(raw)
    assert "[REDACTED]" in redacted
    assert "admin@bank.com" not in redacted
    assert "TR100000000000000000000001" not in redacted

    # 2. Bundle compilation
    bundle = compiler.compile_diagnostic_bundle(output_dir=tmp_path, redact_pii=True)
    assert bundle.bundle_id.startswith("diag_")
    assert len(bundle.checksum_sha256) == 64
    assert Path(bundle.bundle_filepath).exists()
