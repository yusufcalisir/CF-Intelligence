#!/usr/bin/env python3
"""Resilient and Fail-Closed pip-audit Execution & Classification Pipeline.

Authoritative Error Classification Contract:
1. SUCCESS (returncode == 0):
   - Zero unsuppressed vulnerabilities found.
   - Immediately returns 0.

2. VULNERABILITY_FOUND (returncode == 1 or confirmed vulnerability report):
   - Real security vulnerability detected.
   - NEVER treated as transient network failure.
   - NEVER retried.
   - Fails build immediately with exit code 1.

3. TRANSIENT_NETWORK_FAILURE:
   - Returncode non-zero, accompanied by POSITIVE network/service failure markers
     (e.g., timeout, ConnectionError, HTTP 502/503/504/429, RemoteDisconnected).
   - Retries up to max_retries with bounded exponential backoff.
   - Falls back to OSV vulnerability service only when primary PyPI database
     unavailability is positively confirmed.

4. CLI_OR_CONFIG_ERROR:
   - Non-zero returncode without positive network failure indicators
     (e.g., malformed lockfile, invalid arguments, syntax errors).
   - Immediately fails closed without retry or fallback.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Final, Literal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pip_audit_pipeline")

ClassificationResult = Literal[
    "SUCCESS",
    "VULNERABILITY_FOUND",
    "TRANSIENT_NETWORK_FAILURE",
    "CLI_OR_CONFIG_ERROR",
]

# Positive markers indicating a network, timeout, or remote service outage
POSITIVE_NETWORK_MARKERS: Final[tuple[str, ...]] = (
    "timeout",
    "timed out",
    "connectionerror",
    "connecterror",
    "503 server error",
    "502 server error",
    "504 gateway timeout",
    "429 too many requests",
    "service unavailable",
    "remotedisconnected",
    "maxretryerror",
    "newconnectionerror",
    "temporary failure in name resolution",
    "network is unreachable",
    "failed to establish a new connection",
    "connection reset by peer",
    "osv service unavailable",
    "pypi json api unavailable",
)

# Explicit markers indicating pip-audit discovered real vulnerabilities
VULNERABILITY_OUTPUT_MARKERS: Final[tuple[str, ...]] = (
    "known vulnerabilities found",
    "vulnerabilities found",
    "found vulnerabilities",
    "vulnerability",
    "name | installed version | id",
    "ghsa-",
    "cve-",
    "pysec-",
)


def classify_pip_audit_result(returncode: int, stdout: str, stderr: str) -> ClassificationResult:
    """Classify pip-audit returncode and streams under the authoritative security contract."""
    if returncode == 0:
        return "SUCCESS"

    combined = (stdout + "\n" + stderr).lower()

    # 1. Positively identified network/service failure markers
    if any(marker in combined for marker in POSITIVE_NETWORK_MARKERS):
        return "TRANSIENT_NETWORK_FAILURE"

    # 2. Confirmed vulnerability findings
    if any(marker in combined for marker in VULNERABILITY_OUTPUT_MARKERS):
        return "VULNERABILITY_FOUND"

    # 3. Everything else (unpinned requirement, CLI syntax error, invalid argument, unknown error)
    # is classified as CLI_OR_CONFIG_ERROR and fails closed immediately
    return "CLI_OR_CONFIG_ERROR"


def execute_audit_command(cmd: list[str]) -> tuple[int, str, str]:
    """Execute the given pip-audit command and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def build_audit_cmd(
    requirements_file: str,
    service: str = "pypi",
    timeout: int = 30,
    ignored_vulns: list[str] | None = None,
) -> list[str]:
    """Construct the pip-audit CLI command list."""
    cmd = [
        "pip-audit",
        "--no-deps",
        "--disable-pip",
        "-r",
        requirements_file,
        "--timeout",
        str(timeout),
    ]
    if service and service.lower() != "pypi":
        cmd.extend(["-s", service.lower()])

    if ignored_vulns:
        for vuln_id in ignored_vulns:
            cleaned = vuln_id.strip()
            if cleaned:
                cmd.extend(["--ignore-vuln", cleaned])

    return cmd


def run_audit_pipeline(
    requirements_file: str,
    ignored_vulns: list[str] | None = None,
    max_retries: int = 3,
    initial_backoff: float = 10.0,
    allow_osv_fallback: bool = True,
    executor: Callable[[list[str]], tuple[int, str, str]] = execute_audit_command,
) -> int:
    """Run pip-audit with bounded retry on network failure and explicit fail-closed semantics."""
    logger.info("Starting dependency vulnerability audit against '%s'...", requirements_file)
    backoff = initial_backoff

    # 1. Primary PyPI Vulnerability Service Phase
    for attempt in range(1, max_retries + 1):
        cmd = build_audit_cmd(
            requirements_file=requirements_file,
            service="pypi",
            ignored_vulns=ignored_vulns,
        )
        logger.info("[PyPI] Running pip-audit scan (attempt %d/%d)...", attempt, max_retries)
        rc, out, err = executor(cmd)
        classification = classify_pip_audit_result(rc, out, err)

        if classification == "SUCCESS":
            logger.info("✅ pip-audit completed successfully: zero unsuppressed vulnerabilities found.")
            if out.strip():
                print(out)
            return 0

        if classification == "VULNERABILITY_FOUND":
            logger.error("❌ pip-audit discovered confirmed vulnerabilities (returncode=%d).", rc)
            logger.error("Failing build immediately — vulnerability findings are NOT transient network failures.")
            if out.strip():
                print(out)
            if err.strip():
                print(err, file=sys.stderr)
            return 1

        if classification == "CLI_OR_CONFIG_ERROR":
            logger.error("❌ pip-audit failed with CLI/configuration error (returncode=%d).", rc)
            logger.error("Failing closed immediately — unexpected error cannot be retried.")
            if err.strip():
                print(err, file=sys.stderr)
            elif out.strip():
                print(out)
            return rc if rc != 0 else 2

        # classification == "TRANSIENT_NETWORK_FAILURE"
        logger.warning(
            "⚠️ pip-audit attempt %d/%d encountered transient network/service failure (returncode=%d).",
            attempt,
            max_retries,
            rc,
        )
        if attempt < max_retries:
            logger.info("Backing off for %.1f seconds before retry...", backoff)
            time.sleep(backoff)
            backoff *= 1.5

    # 2. Resilient OSV Fallback Phase
    if not allow_osv_fallback:
        logger.error("❌ Primary PyPI vulnerability service unavailable and OSV fallback disabled. Fail-closed.")
        return 2

    logger.warning("⚠️ Primary PyPI service unavailable after %d network attempts.", max_retries)
    logger.info("Executing secondary audit via OSV vulnerability service (-s osv)...")
    osv_cmd = build_audit_cmd(
        requirements_file=requirements_file,
        service="osv",
        ignored_vulns=ignored_vulns,
    )
    rc, out, err = executor(osv_cmd)
    classification = classify_pip_audit_result(rc, out, err)

    if classification == "SUCCESS":
        logger.info("✅ OSV fallback pip-audit completed successfully: zero unsuppressed vulnerabilities found.")
        if out.strip():
            print(out)
        return 0

    if classification == "VULNERABILITY_FOUND":
        logger.error("❌ OSV fallback pip-audit discovered confirmed vulnerabilities (returncode=%d).", rc)
        logger.error("Failing build immediately.")
        if out.strip():
            print(out)
        if err.strip():
            print(err, file=sys.stderr)
        return 1

    logger.error("❌ Both PyPI and OSV vulnerability services failed or returned errors. Fail-closed.")
    if err.strip():
        print(err, file=sys.stderr)
    return rc if rc != 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resilient pip-audit security auditor with fail-closed classification")
    parser.add_argument("-r", "--requirements", required=True, help="Path to locked requirements file")
    parser.add_argument("--ignore-vuln", action="append", default=[], help="Advisory IDs to suppress with justification")
    parser.add_argument("--max-retries", type=int, default=3, help="Max network retries before OSV fallback")
    parser.add_argument("--backoff", type=float, default=10.0, help="Initial retry backoff in seconds")
    parser.add_argument("--no-osv-fallback", action="store_true", help="Disable OSV fallback")

    args = parser.parse_args(argv)
    return run_audit_pipeline(
        requirements_file=args.requirements,
        ignored_vulns=args.ignore_vuln,
        max_retries=args.max_retries,
        initial_backoff=args.backoff,
        allow_osv_fallback=not args.no_osv_fallback,
    )


if __name__ == "__main__":
    sys.exit(main())

