# ruff: noqa: UP042
"""SIEM Log Exporter (Syslog RFC 5424 / CEF / Splunk HEC / Datadog JSON)."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RETRY_QUEUE_FILE = Path(__file__).parent.parent.parent / "storage" / "siem_retry_queue.jsonl"


class SIEMExportError(Exception):
    """Raised when an active SIEM exporter fails to deliver an audit event payload."""

    pass


class SIEMFormat(str, Enum):
    """Supported SIEM output payload formats."""

    CEF_SYSLOG = "CEF_SYSLOG"
    JSON_DATADOG = "JSON_DATADOG"
    SPLUNK_HEC = "SPLUNK_HEC"


@dataclass
class SIEMAuditEvent:
    """Dataclass holding a structured security audit event for SIEM forwarding."""

    event_id: str
    event_type: str
    severity: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    source_bank: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class SIEMLogExporter:
    """Formats security audit events and exports them to Syslog (RFC 5424), Splunk HEC, or Datadog."""

    def __init__(self) -> None:
        self._flusher_thread: threading.Thread | None = None
        self._running = False

    def format_rfc5424_syslog(self, event: dict[str, Any]) -> str:
        """Formats audit dictionary as valid RFC 5424 Syslog message string."""
        pri = 134  # Facility: local0 (16), Severity: Notice (6) -> 16*8 + 6 = 134
        version = 1
        timestamp_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        hostname = socket.gethostname() or "cfi-coordinator"
        app_name = "CFI"
        proc_id = os.getpid()
        msg_id = str(event.get("event", event.get("event_type", "AUDIT_EVENT")))

        msg_body = json.dumps(event)
        return f"<{pri}>{version} {timestamp_iso} {hostname} {app_name} {proc_id} {msg_id} - {msg_body}"

    def export_syslog(self, event: dict[str, Any], host: str | None = None) -> None:
        """Format as RFC 5424 syslog string, send via UDP to host:514 (or TCP 6514 fallback)."""
        syslog_host = host or os.getenv("SIEM_SYSLOG_HOST", "127.0.0.1")
        syslog_msg = self.format_rfc5424_syslog(event).encode("utf-8")

        # Try UDP 514
        udp_success = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2.0)
                sock.sendto(syslog_msg, (syslog_host, 514))
                udp_success = True
        except Exception as e:
            logger.debug("Syslog UDP send failed (%s), attempting TCP 6514 fallback...", e)

        if not udp_success:
            # Fallback to TCP 6514
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(2.0)
                    sock.connect((syslog_host, 6514))
                    sock.sendall(syslog_msg + b"\n")
            except Exception as e:
                logger.error("Syslog export failed via UDP and TCP: %s", e)
                raise SIEMExportError(f"Syslog delivery failed to {syslog_host}: {e}") from e

    def export_splunk(
        self, event: dict[str, Any], hec_url: str | None = None, token: str | None = None
    ) -> None:
        """HTTP POST to {SPLUNK_HEC_URL}/services/collector/event with Authorization header."""
        splunk_url = (hec_url or os.getenv("SPLUNK_HEC_URL") or "").rstrip("/")
        hec_token = token or os.getenv("SPLUNK_HEC_TOKEN", "")

        if not splunk_url:
            raise SIEMExportError("SPLUNK_HEC_URL is unconfigured")

        url = f"{splunk_url}/services/collector/event"
        headers = {
            "Authorization": f"Splunk {hec_token}",
            "Content-Type": "application/json",
        }
        payload = json.dumps(
            {
                "event": event,
                "sourcetype": "cfi:audit:json",
                "source": "cfi_platform",
            }
        ).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:  # nosec B310
                if resp.status not in (200, 201, 202):
                    raise SIEMExportError(f"Splunk HEC returned HTTP {resp.status}")
        except Exception as e:
            logger.error("Splunk HEC export failed: %s", e)
            raise SIEMExportError(f"Splunk export failed: {e}") from e

    def export_datadog(self, event: dict[str, Any], api_key: str | None = None) -> None:
        """HTTP POST to Datadog log intake API."""
        dd_key = api_key or os.getenv("DD_API_KEY", "")
        if not dd_key:
            raise SIEMExportError("DD_API_KEY is unconfigured")

        url = "https://http-intake.logs.datadoghq.com/api/v2/logs"
        headers = {
            "DD-API-KEY": dd_key,
            "Content-Type": "application/json",
        }
        payload = json.dumps(
            [
                {
                    "message": json.dumps(event),
                    "ddsource": "cfi_platform",
                    "service": "cfi_backend",
                }
            ]
        ).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:  # nosec B310
                if resp.status not in (200, 202):
                    raise SIEMExportError(f"Datadog API returned HTTP {resp.status}")
        except Exception as e:
            logger.error("Datadog log export failed: %s", e)
            raise SIEMExportError(f"Datadog export failed: {e}") from e

    def export(self, event: dict[str, Any]) -> None:
        """Dispatch event to all configured exporters based on env vars; if any fails, append to retry queue."""
        exporters_run = 0
        failed = False

        if os.getenv("SIEM_SYSLOG_HOST"):
            exporters_run += 1
            try:
                self.export_syslog(event)
            except SIEMExportError:
                failed = True

        if os.getenv("SPLUNK_HEC_URL"):
            exporters_run += 1
            try:
                self.export_splunk(event)
            except SIEMExportError:
                failed = True

        if os.getenv("DD_API_KEY"):
            exporters_run += 1
            try:
                self.export_datadog(event)
            except SIEMExportError:
                failed = True

        if failed or exporters_run == 0:
            # Append to offline retry queue for background flushing
            self._queue_retry_event(event)

    def _queue_retry_event(self, event: dict[str, Any]) -> None:
        """Append event payload to local storage/siem_retry_queue.jsonl file."""
        try:
            RETRY_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(RETRY_QUEUE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
            logger.info("Queued SIEM event to retry buffer: %s", RETRY_QUEUE_FILE)
        except Exception as e:
            logger.error("Failed to queue SIEM event to retry file: %s", e)

    def flush_retry_queue(self) -> int:
        """Flush queued events in storage/siem_retry_queue.jsonl to active exporters."""
        if not RETRY_QUEUE_FILE.exists():
            return 0

        try:
            lines = RETRY_QUEUE_FILE.read_text(encoding="utf-8").strip().splitlines()
            if not lines:
                return 0

            flushed_count = 0
            remaining_lines: list[str] = []

            for line in lines:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    # Attempt sending via syslog or splunk if configured
                    if os.getenv("SIEM_SYSLOG_HOST") or os.getenv("SPLUNK_HEC_URL"):
                        if os.getenv("SIEM_SYSLOG_HOST"):
                            self.export_syslog(event)
                        if os.getenv("SPLUNK_HEC_URL"):
                            self.export_splunk(event)
                    flushed_count += 1
                except Exception:
                    remaining_lines.append(line)

            if remaining_lines:
                RETRY_QUEUE_FILE.write_text("\n".join(remaining_lines) + "\n", encoding="utf-8")
            else:
                RETRY_QUEUE_FILE.write_text("", encoding="utf-8")

            logger.info(
                "Flushed %d SIEM retry events (%d remaining)", flushed_count, len(remaining_lines)
            )
            return flushed_count
        except Exception as e:
            logger.error("Error flushing SIEM retry queue: %s", e)
            return 0

    def start_retry_flusher(self, interval_seconds: int = 60) -> None:
        """Start background thread that periodically flushes retry queue every interval_seconds."""
        if self._running:
            return

        self._running = True

        def _flusher_loop() -> None:
            while self._running:
                time.sleep(interval_seconds)
                self.flush_retry_queue()

        self._flusher_thread = threading.Thread(target=_flusher_loop, daemon=True)
        self._flusher_thread.start()
        logger.info(
            "Started SIEM retry flusher background daemon thread (interval: %ds)", interval_seconds
        )

    def stop_retry_flusher(self) -> None:
        """Stop background retry flusher thread."""
        self._running = False

    def format_cef_event(self, event: SIEMAuditEvent) -> str:
        """Formats audit event into Common Event Format (CEF)."""
        severity_map = {"LOW": "1", "MEDIUM": "4", "HIGH": "7", "CRITICAL": "10"}
        cef_sev = severity_map.get(event.severity.upper(), "5")
        ts_str = event.timestamp.isoformat()
        return (
            f"CEF:0|CFI|Simulator|2.0|{event.event_type}|{event.message}|{cef_sev}|"
            f"eventId={event.event_id} srcBank={event.source_bank} rt={ts_str}"
        )

    def export_event(
        self,
        event: SIEMAuditEvent,
        format_type: SIEMFormat = SIEMFormat.CEF_SYSLOG,
    ) -> str:
        """Exports audit event in requested SIEM payload format."""
        if format_type == SIEMFormat.CEF_SYSLOG:
            formatted = self.format_cef_event(event)
        elif format_type == SIEMFormat.JSON_DATADOG:
            formatted = json.dumps(
                {
                    "ddsource": "cfi_simulator",
                    "ddtags": f"env:production,bank:{event.source_bank}",
                    "hostname": "cfi-coordinator",
                    "message": event.message,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "status": event.severity.lower(),
                    "timestamp": event.timestamp.isoformat(),
                },
                indent=2,
            )
        else:  # SPLUNK_HEC
            formatted = json.dumps(
                {
                    "event": {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "message": event.message,
                        "bank": event.source_bank,
                    },
                    "sourcetype": "cfi:audit:json",
                    "source": "cfi_simulator",
                },
                indent=2,
            )

        logger.info("Exported SIEM audit event %s (%s format)", event.event_id, format_type.value)
        return formatted


siem_exporter = SIEMLogExporter()
