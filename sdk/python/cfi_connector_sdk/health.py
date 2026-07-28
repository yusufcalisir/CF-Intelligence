"""Connector Health Check Protocol and Probe Monitor for CFI Bank Nodes."""

from __future__ import annotations

import logging
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ConnectorHealthStatus:
    """Dataclass holding operational health metrics for a bank node connector."""

    status: str = "HEALTHY"  # HEALTHY, DEGRADED, UNHEALTHY
    broker_connected: bool = True
    cert_days_remaining: int = 365
    last_ping_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    details: dict[str, Any] = field(default_factory=dict)


class ConnectorHealthMonitor:
    """Probes message broker connectivity, mTLS certificate validity, and node health."""

    def __init__(
        self,
        broker_host: str = "localhost",
        broker_port: int = 5672,
        cert_path: str | None = None,
    ) -> None:
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.cert_path = cert_path

    def check_broker_ping(self, timeout: float = 2.0) -> bool:
        """Performs TCP connection ping to check message broker reachability."""
        try:
            with socket.create_connection(
                (self.broker_host, self.broker_port), timeout=timeout
            ):
                return True
        except (OSError, socket.timeout, ConnectionRefusedError) as exc:
            logger.warning(
                "Broker ping failed for %s:%d - %s",
                self.broker_host,
                self.broker_port,
                exc,
            )
            return False

    def check_cert_validity(self) -> int:
        """Reads X.509 certificate and calculates days remaining until expiration."""
        if not self.cert_path:
            return 365  # Default fallback if cert path is not configured

        try:
            cert_dict = ssl._ssl._test_decode_cert(self.cert_path)
            not_after_str = cert_dict.get("notAfter", "")
            if not_after_str:
                # Format: 'MMM DD HH:MM:SS YYYY GMT'
                expire_dt = datetime.strptime(
                    not_after_str, "%b %d %H:%M:%S %Y %Z"
                ).replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                days_left = (expire_dt - now).days
                return max(days_left, 0)
        except Exception as exc:
            logger.warning(
                "Failed to parse X.509 certificate at %s: %s", self.cert_path, exc
            )

        return 30  # Default safe estimate on parse error

    def get_health_report(self) -> ConnectorHealthStatus:
        """Generates overall node operational health report."""
        broker_ok = self.check_broker_ping()
        cert_days = self.check_cert_validity()

        overall_status = "HEALTHY"
        if not broker_ok:
            overall_status = "UNHEALTHY"
        elif cert_days < 7:
            overall_status = "DEGRADED"

        return ConnectorHealthStatus(
            status=overall_status,
            broker_connected=broker_ok,
            cert_days_remaining=cert_days,
            details={
                "broker_target": f"{self.broker_host}:{self.broker_port}",
                "cert_configured": bool(self.cert_path),
            },
        )
