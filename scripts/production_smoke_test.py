#!/usr/bin/env python3
"""Production Smoke Test Script — Phase 43.2.

Executes post-deployment automated health checks, SLA validation, cron status,
and OpenAPI documentation availability against a target environment.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def run_check(
    name: str,
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
    expected_status: int = 200,
    expected_key: str | None = None,
    expected_value: Any | None = None,
    must_be_list: bool = False,
) -> bool:
    """Executes a single HTTP check and prints formatted status."""
    req_headers = headers or {}
    data = None

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

    start_time = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            status_code = response.getcode()
            body_bytes = response.read()

            if status_code != expected_status:
                print(f"[FAIL] | {name} -- Received HTTP {status_code}, expected {expected_status}")
                return False

            if expected_key or must_be_list:
                try:
                    res_json = json.loads(body_bytes.decode("utf-8"))
                    if must_be_list:
                        if not isinstance(res_json, list):
                            print(
                                f"[FAIL] | {name} -- Response body is not a list ({type(res_json).__name__})"
                            )
                            return False
                    elif expected_key:
                        if expected_key not in res_json:
                            print(
                                f"[FAIL] | {name} -- Key '{expected_key}' missing from response JSON"
                            )
                            return False
                        if expected_value is not None and res_json[expected_key] != expected_value:
                            print(
                                f"[FAIL] | {name} -- {expected_key} = {res_json[expected_key]!r}, expected {expected_value!r}"
                            )
                            return False
                except json.JSONDecodeError:
                    print(f"[FAIL] | {name} -- Response is not valid JSON")
                    return False

            print(
                f"[PASS] | {name} -- HTTP {status_code} ({elapsed_ms:.1f}ms) [{url}]"
            )
            return True

    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        print(
            f"[FAIL] | {name} -- HTTP {e.code} error ({elapsed_ms:.1f}ms): {e.reason}"
        )
        return False
    except urllib.error.URLError as e:
        print(f"[FAIL] | {name} -- Network/URL error: {e.reason}")
        return False
    except Exception as e:
        print(f"[FAIL] | {name} -- Unexpected error: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run production smoke tests against a target deployment base URL."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the target API instance (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP request timeout in seconds (default: 10.0)",
    )
    parser.add_argument(
        "--cron-secret",
        default="cfi_cron_secret_secure_token_2026",
        help="Secret header key for maintenance cron endpoints",
    )

    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    print("=========================================================")
    print(f"Running Production Smoke Test Suite -> {base_url}")
    print("=========================================================")

    results: list[bool] = []

    # Check 1: GET /health
    results.append(
        run_check(
            name="Check 1: System Health Endpoint",
            url=f"{base_url}/health",
            timeout=args.timeout,
            expected_key="status",
            expected_value="healthy",
        )
    )

    # Check 2: GET /v1/admin/banks/
    results.append(
        run_check(
            name="Check 2: Bank Registry Admin Endpoint",
            url=f"{base_url}/v1/admin/banks/",
            timeout=args.timeout,
            must_be_list=True,
        )
    )

    # Check 3: POST /v1/inference/score
    inference_payload = {
        "transaction_id": "tx_smoke_test_001",
        "amount": 250.00,
        "currency": "USD",
        "source_account": "acc_smoke_alpha",
        "target_account": "acc_smoke_beta",
        "merchant_category": "general_retail",
        "velocity_1h": 1,
        "force_fallback": False,
    }
    results.append(
        run_check(
            name="Check 3: Real-Time Inference SLA Endpoint",
            url=f"{base_url}/v1/inference/score",
            method="POST",
            payload=inference_payload,
            timeout=args.timeout,
            expected_key="decision",
        )
    )

    # Check 4: GET /v1/cron/health-check
    cron_headers = {"X-Cron-Secret": args.cron_secret}
    results.append(
        run_check(
            name="Check 4: Maintenance Cron Health Diagnostic",
            url=f"{base_url}/v1/cron/health-check",
            headers=cron_headers,
            timeout=args.timeout,
            expected_key="status",
            expected_value="HEALTHY",
        )
    )

    # Check 5: GET /docs
    results.append(
        run_check(
            name="Check 5: OpenAPI Documentation Availability",
            url=f"{base_url}/docs",
            timeout=args.timeout,
        )
    )

    print("---------------------------------------------------------")
    passed_count = sum(results)
    total_count = len(results)

    if all(results):
        print(f"ALL {total_count}/{total_count} SMOKE CHECKS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print(
            f"SMOKE TEST FAILED: {passed_count}/{total_count} checks passed."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
