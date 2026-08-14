"""Master Unified Test Runner for CF-Intelligence.

Executes all test suites across the repository:
1. Frontend Suite (Vitest: 57 test files, 125 integration/view/component/E2E tests)
2. Frontend Visual Regression Suite (Playwright: 36 visual snapshot tests across 4 viewports)
3. Backend Suite (Pytest: 1023+ unit, integration, chaos, and property-based tests)
4. Scientific Verification Suite (Pytest + Reference audits across all 17 modules)
5. EVM Smart Contracts Suite (Hardhat: Shapley token settlements)

Usage:
    python scripts/run_all_tests.py              # Run frontend + backend + verification
    python scripts/run_all_tests.py --frontend   # Run frontend unit & e2e tests only
    python scripts/run_all_tests.py --visual     # Run Playwright visual regression tests only
    python scripts/run_all_tests.py --backend    # Run backend tests only
    python scripts/run_all_tests.py --verification # Run scientific verification only
    python scripts/run_all_tests.py --contracts  # Run smart contracts only
    python scripts/run_all_tests.py --all        # Run all test suites including visual & contracts
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def print_banner(title: str) -> None:
    line = "=" * 78
    print(f"\n{line}\n  {title}\n{line}")


def run_frontend_tests() -> bool:
    print_banner("1. RUNNING FRONTEND TEST SUITE (Vitest: Unit, Integration & E2E)")
    cmd = ["npm", "--prefix", "frontend", "test"]
    start = time.perf_counter()
    res = subprocess.run(cmd, cwd=REPO_ROOT, shell=True)
    duration = time.perf_counter() - start
    success = res.returncode == 0
    status = "PASSED" if success else "FAILED"
    print(f"\n>> Frontend Test Suite {status} in {duration:.2f}s")
    return success


def run_visual_tests() -> bool:
    print_banner("2. RUNNING FRONTEND VISUAL REGRESSION SUITE (Playwright VRT)")
    cmd = ["npm", "--prefix", "frontend", "run", "test:visual"]
    start = time.perf_counter()
    res = subprocess.run(cmd, cwd=REPO_ROOT, shell=True)
    duration = time.perf_counter() - start
    success = res.returncode == 0
    status = "PASSED" if success else "FAILED"
    print(f"\n>> Frontend Visual Regression Suite {status} in {duration:.2f}s")
    return success


def run_backend_tests() -> bool:
    print_banner("3. RUNNING BACKEND TEST SUITE (Pytest)")
    cmd = [sys.executable, "-m", "pytest", "backend/tests/", "-v"]
    start = time.perf_counter()
    res = subprocess.run(cmd, cwd=REPO_ROOT)
    duration = time.perf_counter() - start
    success = res.returncode == 0
    status = "PASSED" if success else "FAILED"
    print(f"\n>> Backend Test Suite {status} in {duration:.2f}s")
    return success


def run_verification_tests() -> bool:
    print_banner("4. RUNNING SCIENTIFIC VERIFICATION SUITE")
    cmd = [sys.executable, "-m", "pytest", "verification/", "-v"]
    start = time.perf_counter()
    res = subprocess.run(cmd, cwd=REPO_ROOT)
    duration = time.perf_counter() - start
    success = res.returncode == 0
    status = "PASSED" if success else "FAILED"
    print(f"\n>> Scientific Verification Suite {status} in {duration:.2f}s")
    return success


def run_contract_tests() -> bool:
    print_banner("5. RUNNING EVM SMART CONTRACTS TEST SUITE (Hardhat)")
    cmd = ["npm", "--prefix", "contracts", "test"]
    start = time.perf_counter()
    res = subprocess.run(cmd, cwd=REPO_ROOT, shell=True)
    duration = time.perf_counter() - start
    success = res.returncode == 0
    status = "PASSED" if success else "FAILED"
    print(f"\n>> Smart Contracts Suite {status} in {duration:.2f}s")
    return success


def main() -> int:
    parser = argparse.ArgumentParser(description="Master Unified Test Runner for CF-Intelligence")
    parser.add_argument("--frontend", action="store_true", help="Run frontend unit & E2E tests only")
    parser.add_argument("--visual", action="store_true", help="Run frontend visual regression tests only")
    parser.add_argument("--backend", action="store_true", help="Run backend tests only")
    parser.add_argument("--verification", action="store_true", help="Run verification tests only")
    parser.add_argument("--contracts", action="store_true", help="Run smart contract tests only")
    parser.add_argument("--all", action="store_true", help="Run all test suites including visual & contracts")

    args = parser.parse_args()

    # Default: run frontend and backend if no specific flags
    is_default = not args.frontend and not args.visual and not args.backend and not args.verification and not args.contracts and not args.all
    run_fe = args.frontend or args.all or is_default
    run_vrt = args.visual or args.all
    run_be = args.backend or args.all or is_default
    run_ver = args.verification or args.all
    run_ct = args.contracts or args.all

    results: dict[str, bool] = {}
    overall_start = time.perf_counter()

    if run_fe:
        results["Frontend (Vitest)"] = run_frontend_tests()
    if run_vrt:
        results["Frontend (Visual VRT)"] = run_visual_tests()
    if run_be:
        results["Backend (Pytest)"] = run_backend_tests()
    if run_ver:
        results["Verification (Audit)"] = run_verification_tests()
    if run_ct:
        results["Smart Contracts (Hardhat)"] = run_contract_tests()

    total_duration = time.perf_counter() - overall_start

    print_banner("TEST EXECUTION SUMMARY REPORT")
    all_passed = True
    for suite, passed in results.items():
        tag = "[PASS]" if passed else "[FAIL]"
        verdict = "PASSED" if passed else "FAILED"
        if not passed:
            all_passed = False
        print(f"  {tag:<8} {suite:<30}: {verdict}")

    print(f"\nTotal Execution Time: {total_duration:.2f}s")
    if all_passed:
        print("\nALL TEST SUITES PASSED CLEANLY (100% SUCCESS)!\n")
        return 0
    else:
        print("\nSOME TEST SUITES REPORTED FAILURES.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
