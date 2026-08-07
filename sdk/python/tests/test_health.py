"""Unit tests for ConnectorHealthMonitor and ConnectorHealthStatus."""

from unittest.mock import patch
import pytest
from cfi_connector_sdk.health import ConnectorHealthMonitor, ConnectorHealthStatus


def test_connector_health_status_defaults():
    status = ConnectorHealthStatus()
    assert status.status == "HEALTHY"
    assert status.broker_connected is True
    assert status.cert_days_remaining == 365
    assert status.last_ping_timestamp is not None


@patch.object(ConnectorHealthMonitor, "check_broker_ping", return_value=True)
@patch.object(ConnectorHealthMonitor, "check_cert_validity", return_value=365)
def test_health_monitor_report_healthy(mock_cert, mock_ping):
    monitor = ConnectorHealthMonitor(broker_host="127.0.0.1", broker_port=5672)
    report = monitor.get_health_report()

    assert report.status == "HEALTHY"
    assert report.broker_connected is True
    assert report.cert_days_remaining == 365


@patch.object(ConnectorHealthMonitor, "check_broker_ping", return_value=False)
@patch.object(ConnectorHealthMonitor, "check_cert_validity", return_value=365)
def test_health_monitor_report_unhealthy_broker(mock_cert, mock_ping):
    monitor = ConnectorHealthMonitor(broker_host="127.0.0.1", broker_port=5672)
    report = monitor.get_health_report()

    assert report.status == "UNHEALTHY"
    assert report.broker_connected is False


@patch.object(ConnectorHealthMonitor, "check_broker_ping", return_value=True)
@patch.object(ConnectorHealthMonitor, "check_cert_validity", return_value=3)
def test_health_monitor_report_degraded_cert(mock_cert, mock_ping):
    monitor = ConnectorHealthMonitor(broker_host="127.0.0.1", broker_port=5672)
    report = monitor.get_health_report()

    assert report.status == "DEGRADED"
    assert report.broker_connected is True
    assert report.cert_days_remaining == 3
