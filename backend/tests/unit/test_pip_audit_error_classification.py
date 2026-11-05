"""Unit tests for resilient and fail-closed pip-audit error classification."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path for scripts module import
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_pip_audit import (  # noqa: E402
    classify_pip_audit_result,
    run_audit_pipeline,
)


def test_classify_success() -> None:
    """Returncode 0 is classified as SUCCESS."""
    assert classify_pip_audit_result(0, "No known vulnerabilities found", "") == "SUCCESS"


def test_classify_vulnerability_found() -> None:
    """Confirmed vulnerability table with returncode 1 is classified as VULNERABILITY_FOUND."""
    stdout = """
Name    Installed Version ID                  Fix Versions
------- ----------------- ------------------- ------------
urllib3 1.26.4            GHSA-r64q-w8jr-3hhg 1.26.5
"""
    assert classify_pip_audit_result(1, stdout, "") == "VULNERABILITY_FOUND"


def test_classify_vulnerability_found_with_vulnerability_marker() -> None:
    """Output with vulnerability markers and returncode 1 is treated as a security finding."""
    assert classify_pip_audit_result(1, "Known vulnerabilities found in packages", "") == "VULNERABILITY_FOUND"


def test_classify_cli_error_unpinned_requirement_fails_closed() -> None:
    """Unpinned requirement with returncode 1 is classified as CLI_OR_CONFIG_ERROR."""
    stderr = "ERROR:pip_audit._cli:requirement fastapi is not pinned to an exact version"
    assert classify_pip_audit_result(1, "", stderr) == "CLI_OR_CONFIG_ERROR"


def test_classify_transient_network_timeout() -> None:
    """Connection timeout or remote service error is classified as TRANSIENT_NETWORK_FAILURE."""
    stderr = "urllib3.exceptions.MaxRetryError: HTTPSConnectionPool(host='pypi.org'): Read timed out. (read timeout=30)"
    assert classify_pip_audit_result(2, "", stderr) == "TRANSIENT_NETWORK_FAILURE"


def test_classify_transient_503_service_unavailable() -> None:
    """HTTP 503 from PyPI is classified as TRANSIENT_NETWORK_FAILURE."""
    stderr = "requests.exceptions.HTTPError: 503 Server Error: Service Unavailable for url: https://pypi.org/pypi/sample/json"
    assert classify_pip_audit_result(1, "", stderr) == "TRANSIENT_NETWORK_FAILURE"


def test_classify_cli_error() -> None:
    """Invalid CLI option or file error is classified as CLI_OR_CONFIG_ERROR."""
    stderr = "pip-audit: error: unrecognized arguments: --invalid-option"
    assert classify_pip_audit_result(2, "", stderr) == "CLI_OR_CONFIG_ERROR"


def test_pipeline_fails_immediately_on_vulnerability() -> None:
    """Vulnerability finding must exit 1 immediately without retrying."""
    call_count = 0

    def mock_executor(cmd: list[str]) -> tuple[int, str, str]:
        nonlocal call_count
        call_count += 1
        return 1, "urllib3 1.26.4 GHSA-r64q-w8jr-3hhg", ""

    rc = run_audit_pipeline(
        requirements_file="dummy.lock",
        max_retries=3,
        initial_backoff=0.01,
        executor=mock_executor,
    )
    assert rc == 1
    assert call_count == 1, "Must fail on first attempt without retrying vulnerabilities"


def test_pipeline_fails_immediately_on_cli_error() -> None:
    """CLI syntax or configuration error must exit immediately without retrying."""
    call_count = 0

    def mock_executor(cmd: list[str]) -> tuple[int, str, str]:
        nonlocal call_count
        call_count += 1
        return 2, "", "error: unrecognized arguments: --foo"

    rc = run_audit_pipeline(
        requirements_file="dummy.lock",
        max_retries=3,
        initial_backoff=0.01,
        executor=mock_executor,
    )
    assert rc == 2
    assert call_count == 1, "Must fail on first attempt without retrying CLI errors"


def test_pipeline_retries_transient_network_and_falls_back_to_osv() -> None:
    """Network failure retries up to max_retries and succeeds on OSV fallback."""
    calls: list[list[str]] = []

    def mock_executor(cmd: list[str]) -> tuple[int, str, str]:
        calls.append(cmd)
        if "-s" in cmd and "osv" in cmd:
            return 0, "No known vulnerabilities found", ""
        return 2, "", "Read timed out"

    rc = run_audit_pipeline(
        requirements_file="dummy.lock",
        max_retries=3,
        initial_backoff=0.01,
        executor=mock_executor,
    )
    assert rc == 0
    assert len(calls) == 4  # 3 PyPI retries + 1 OSV fallback
    assert "-s" in calls[-1] and "osv" in calls[-1]


def test_pipeline_fails_closed_when_both_services_fail() -> None:
    """If both PyPI and OSV fail, the pipeline fails closed."""
    def mock_executor(cmd: list[str]) -> tuple[int, str, str]:
        return 2, "", "ConnectionError: connection timed out"

    rc = run_audit_pipeline(
        requirements_file="dummy.lock",
        max_retries=2,
        initial_backoff=0.01,
        executor=mock_executor,
    )
    assert rc != 0
