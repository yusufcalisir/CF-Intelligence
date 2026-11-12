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
    "known vulnerabilities",
    "known vulnerability",
    "vulnerabilities found",
    "found vulnerabilities",
    "vulnerability",
    "name | installed version | id",
    "ghsa-",
    "cve-",
    "pysec-",
    '"vulns": [{',
    '"vulns":[{',
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
    requirements_file: str | None = None,
    *,
    path: str | None = None,
    local_env: bool = False,
    strict: bool = True,
    skip_editable: bool = False,
    output_format: str = "columns",
    output_file: str | None = None,
    service: str = "pypi",
    timeout: int = 30,
    ignored_vulns: list[str] | None = None,
) -> list[str]:
    """Construct the pip-audit CLI command list for lockfile or live-environment auditing."""
    if requirements_file and (path or local_env):
        raise ValueError("requirements_file cannot be combined with runtime path/local_env auditing.")
    if not requirements_file and not path and not local_env:
        raise ValueError("Either requirements_file or a runtime target (path/local_env) is required.")

    cmd = [sys.executable, "-m", "pip_audit"] if not requirements_file else ["pip-audit"]

    if requirements_file:
        cmd.extend(["--no-deps", "--disable-pip", "-r", requirements_file])
    else:
        if local_env:
            cmd.append("--local")
        if path:
            cmd.extend(["--path", path])
        if strict:
            cmd.append("--strict")
        if skip_editable:
            cmd.append("--skip-editable")

    cmd.extend(["--progress-spinner", "off", "--timeout", str(timeout)])

    if output_format and output_format != "columns":
        cmd.extend(["-f", output_format])
    if output_file:
        cmd.extend(["-o", output_file])

    if service and service.lower() != "pypi":
        cmd.extend(["-s", service.lower()])

    if ignored_vulns:
        for vuln_id in ignored_vulns:
            cleaned = vuln_id.strip()
            if cleaned:
                cmd.extend(["--ignore-vuln", cleaned])

    return cmd


def run_audit_pipeline(
    requirements_file: str | None = None,
    *,
    path: str | None = None,
    local_env: bool = False,
    strict: bool = True,
    skip_editable: bool = False,
    output_format: str = "columns",
    output_file: str | None = None,
    ignored_vulns: list[str] | None = None,
    max_retries: int = 3,
    initial_backoff: float = 10.0,
    allow_osv_fallback: bool = True,
    executor: Callable[[list[str]], tuple[int, str, str]] = execute_audit_command,
) -> int:
    """Run pip-audit with bounded retry on network failure and explicit fail-closed semantics."""
    target_desc = requirements_file or (f"runtime path '{path}'" if path else "local environment")
    logger.info("Starting dependency vulnerability audit against %s...", target_desc)
    backoff = initial_backoff

    cmd_kwargs: dict[str, object] = {
        "requirements_file": requirements_file,
        "path": path,
        "local_env": local_env,
        "strict": strict,
        "skip_editable": skip_editable,
        "output_format": output_format,
        "output_file": output_file,
        "ignored_vulns": ignored_vulns,
    }

    # 1. Primary PyPI Vulnerability Service Phase
    for attempt in range(1, max_retries + 1):
        cmd = build_audit_cmd(service="pypi", **cmd_kwargs)  # type: ignore[arg-type]
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
    osv_cmd = build_audit_cmd(service="osv", **cmd_kwargs)  # type: ignore[arg-type]
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
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("-r", "--requirements", help="Path to locked requirements file")
    target_group.add_argument("--path", help="Path to runtime site-packages directory (e.g. /opt/venv/lib/python3.12/site-packages)")
    target_group.add_argument("--local", action="store_true", help="Audit currently active local Python environment")
    parser.add_argument("--strict", dest="strict", action="store_true", default=True, help="Fail audit if any installed package cannot be collected (default: enabled for runtime mode)")
    parser.add_argument("--no-strict", dest="strict", action="store_false", help="Disable --strict mode when explicitly requested")
    parser.add_argument("--skip-editable", action="store_true", help="Skip editable packages (disabled by default to prevent silent omissions)")
    parser.add_argument("-f", "--format", default="columns", choices=["columns", "json", "cyclonedx-json", "cyclonedx-xml", "markdown"], help="pip-audit output format")
    parser.add_argument("-o", "--output", default=None, help="Write audit report to file")
    parser.add_argument("--ignore-vuln", action="append", default=[], help="Advisory IDs to suppress with justification")
    parser.add_argument("--max-retries", type=int, default=3, help="Max network retries before OSV fallback")
    parser.add_argument("--backoff", type=float, default=10.0, help="Initial retry backoff in seconds")
    parser.add_argument("--no-osv-fallback", action="store_true", help="Disable OSV fallback")

    args = parser.parse_args(argv)
    return run_audit_pipeline(
        requirements_file=args.requirements,
        path=args.path,
        local_env=args.local,
        strict=args.strict,
        skip_editable=args.skip_editable,
        output_format=args.format,
        output_file=args.output,
        ignored_vulns=args.ignore_vuln,
        max_retries=args.max_retries,
        initial_backoff=args.backoff,
        allow_osv_fallback=not args.no_osv_fallback,
    )


if __name__ == "__main__":
    sys.exit(main())

